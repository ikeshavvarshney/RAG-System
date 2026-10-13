"""Evaluation dashboard API: read RAGAS run results and start a run of eval/run_eval.py as a child process."""

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.schemas import ERRORS

logger = logging.getLogger(__name__)

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
REFERENCE_PATH = REPO_ROOT / "data" / "eval" / "reference_set.json"
HARNESS_PATH = REPO_ROOT / "eval" / "run_eval.py"
ENV_PATH = REPO_ROOT / "backend" / ".env"

RUN_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
QUESTION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"
METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
LOG_TAIL_LINES = 40

# Exit codes of eval/run_eval.py.
EXIT_MESSAGES = {
    0: "Finished. Every question was scored.",
    1: "Finished, but the judge could not score some questions.",
    2: "The run was rejected: no questions matched, or a setting was invalid.",
    3: "Stopped before scoring: an answer came from the answer cache. Clear the cache and run again.",
    4: "A run with this name already has results. Resume it or choose another name.",
    5: "Could not resume: the stored settings differ from the current ones.",
}


class StartRunRequest(BaseModel):
    run_name: str = Field(pattern=RUN_NAME_PATTERN)
    limit: int | None = Field(default=None, ge=1, le=500)
    ids: list[str] | None = Field(default=None, max_length=200)
    preset: Literal["naive"] | None = None
    dense_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    resume: bool = False


@dataclass
class _Job:
    run_name: str
    process: Any
    started_at: float
    total: int
    log_path: Path
    command: list[str]
    stopped: bool = False


_job: _Job | None = None


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _reference_questions() -> list[dict]:
    data = _read_json(REFERENCE_PATH)
    entries = data.get("questions", []) if isinstance(data, dict) else data
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def _judge_configured() -> bool:
    if os.environ.get("GROQ_EVAL_API_KEYS", "").strip():
        return True
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        name, sep, value = line.partition("=")
        value = value.strip().strip("\"'")
        if sep and name.strip() == "GROQ_EVAL_API_KEYS" and value and "PASTE" not in value:
            return True
    return False


def _run_names() -> list[str]:
    if not RESULTS_DIR.is_dir():
        return []
    names = {path.name.removesuffix(".raw.json") for path in RESULTS_DIR.glob("*.raw.json")}
    names |= {path.name.removesuffix(".scored.json") for path in RESULTS_DIR.glob("*.scored.json")}
    return sorted(names)


def _score_rows(name: str) -> list[dict]:
    return [row for row in _read_jsonl(RESULTS_DIR / f"{name}.scores.jsonl") if "id" in row]


def _run_summary(name: str) -> dict:
    scored_path = RESULTS_DIR / f"{name}.scored.json"
    raw_path = RESULTS_DIR / f"{name}.raw.json"
    scored = _read_json(scored_path) if scored_path.exists() else None
    newest = max((p.stat().st_mtime for p in (scored_path, raw_path) if p.exists()), default=0.0)
    if isinstance(scored, dict):
        overall = (scored.get("aggregate") or {}).get("overall") or {}
        config = scored.get("config") or {}
        return {
            "name": name,
            "status": "scored",
            "updated_at": newest,
            "questions": (scored.get("split") or {}).get("questions", len(scored.get("per_question") or [])),
            "judge_model": scored.get("judge_model"),
            "preset": config.get("preset"),
            "scores": {metric: overall.get(metric) for metric in METRICS},
            "corpus_coverage_percent": (scored.get("split") or {}).get("corpus_coverage_percent"),
        }
    raw = _read_json(raw_path) or {}
    return {
        "name": name,
        "status": "partial",
        "updated_at": newest,
        "questions": len(raw.get("questions") or []),
        "judge_model": None,
        "preset": (raw.get("config") or {}).get("preset"),
        "scores": {metric: None for metric in METRICS},
        "corpus_coverage_percent": None,
    }


def _question_detail(record: dict, row: dict | None) -> dict:
    citations = record.get("citations") or []
    return {
        "id": record.get("id"),
        "category": record.get("category"),
        "question": record.get("question"),
        "reference_answer": record.get("ground_truth"),
        "answer": record.get("answer"),
        "answer_source": record.get("answer_source"),
        "visual_dependent": bool(record.get("requires_tables_charts")),
        "error": record.get("error"),
        "terminated_at": record.get("terminated_at"),
        "wall_clock_sec": record.get("wall_clock_sec"),
        "context_count": len(record.get("contexts") or []),
        "citation_count": len(citations),
        "status": row.get("status") if row else "unscored",
        "scores": (row.get("scores") if row else None) or {metric: None for metric in METRICS},
        "failures": (row.get("failures") if row else None) or [],
        "judge_calls": row.get("judge_calls") if row else None,
        "judge_seconds": row.get("seconds") if row else None,
        "judge_tokens": (row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)) if row else None,
    }


def _log_tail(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-LOG_TAIL_LINES:]


def _job_status() -> dict:
    job = _job
    if job is None:
        return {"state": "idle"}
    code = job.process.poll()
    raw = _read_json(RESULTS_DIR / f"{job.run_name}.raw.json") or {}
    answered = len([r for r in raw.get("questions") or [] if not r.get("error")])
    scored = len(_score_rows(job.run_name))
    status = {
        "run_name": job.run_name,
        "started_at": job.started_at,
        "elapsed_sec": round(time.time() - job.started_at, 1),
        "total": job.total,
        "answered": min(answered, job.total),
        "scored": min(scored, job.total),
        "command": " ".join(job.command),
        "log": _log_tail(job.log_path),
    }
    if code is None:
        status["state"] = "running"
        status["phase"] = "answering" if answered < job.total else "scoring"
        return status
    status["state"] = "stopped" if job.stopped else ("finished" if code in (0, 1) else "failed")
    status["exit_code"] = code
    status["message"] = "Stopped before it finished." if job.stopped else EXIT_MESSAGES.get(
        code, f"The run exited with code {code}. See the log below."
    )
    return status


def _command(request: StartRunRequest) -> list[str]:
    command = [sys.executable, str(HARNESS_PATH), "--run-name", request.run_name]
    if request.limit is not None:
        command += ["--limit", str(request.limit)]
    if request.ids:
        command += ["--ids", ",".join(request.ids)]
    if request.preset:
        command += ["--preset", request.preset]
    if request.dense_weight is not None:
        command += ["--set", f"FUSION_DENSE_WEIGHT={request.dense_weight}"]
    if request.resume:
        command.append("--resume")
    return command


def _display_command(command: list[str]) -> list[str]:
    return ["python", "eval/run_eval.py", *command[2:]]


@router.get("/eval/reference", responses=ERRORS)
async def reference_summary() -> dict:
    """How the reference question set is composed."""
    questions = _reference_questions()
    per_category: dict[str, int] = {}
    for question in questions:
        category = question.get("category") or "uncategorised"
        per_category[category] = per_category.get(category, 0) + 1
    return {
        "questions": len(questions),
        "per_category": per_category,
        "visual_dependent": len([q for q in questions if q.get("visual_dependent")]),
        "ids": [q.get("id") for q in questions if q.get("id")],
        "judge_configured": _judge_configured(),
    }


@router.get("/eval/runs", responses=ERRORS)
async def list_runs() -> dict:
    """Every run with results on disk, newest first."""
    runs = [_run_summary(name) for name in _run_names()]
    runs.sort(key=lambda run: run["updated_at"], reverse=True)
    return {"runs": runs}


@router.get("/eval/runs/{name}", responses=ERRORS)
async def get_run(name: str) -> dict:
    """One run: the harness's scored summary plus, per question, the question, the answer and the scores."""
    if name not in _run_names():
        raise HTTPException(status_code=404, detail=f"No evaluation run named {name!r}")
    scored = _read_json(RESULTS_DIR / f"{name}.scored.json")
    raw = _read_json(RESULTS_DIR / f"{name}.raw.json") or {}
    rows = {row["id"]: row for row in _score_rows(name)}
    if isinstance(scored, dict):
        rows.update({row["id"]: row for row in scored.get("per_question") or [] if "id" in row})
    scored = scored if isinstance(scored, dict) else {}
    return {
        "name": name,
        "status": "scored" if scored else "partial",
        "judge_model": scored.get("judge_model"),
        "config": scored.get("config") or raw.get("config"),
        "split": scored.get("split"),
        "aggregate": scored.get("aggregate"),
        "judge_usage": scored.get("judge_usage"),
        "skipped": scored.get("skipped") or [],
        "questions": [_question_detail(record, rows.get(record.get("id"))) for record in raw.get("questions") or []],
    }


@router.get("/eval/status", responses=ERRORS)
async def run_status() -> dict:
    """The run started from the dashboard, if any: its progress and the tail of its log."""
    return _job_status()


@router.post("/eval/runs", status_code=202, responses=ERRORS)
async def start_run(request: StartRunRequest) -> dict:
    """Start eval/run_eval.py in the background. One run at a time."""
    global _job
    if _job is not None and _job.process.poll() is None:
        raise HTTPException(status_code=409, detail=f"Run {_job.run_name!r} is still in progress")
    if not _judge_configured():
        raise HTTPException(
            status_code=409, detail="No judge key is configured. Set GROQ_EVAL_API_KEYS in backend/.env first."
        )
    if request.ids:
        for question_id in request.ids:
            if not re.fullmatch(QUESTION_ID_PATTERN, question_id):
                raise HTTPException(status_code=400, detail=f"Invalid question id {question_id!r}")
    if request.run_name in _run_names() and not request.resume:
        raise HTTPException(
            status_code=409,
            detail=f"Run {request.run_name!r} already has results. Resume it or choose another name.",
        )

    known = [q.get("id") for q in _reference_questions()]
    selected = [i for i in known if i in set(request.ids)] if request.ids else known
    total = len(selected[: request.limit] if request.limit else selected)
    if total == 0:
        raise HTTPException(status_code=400, detail="No questions match that selection")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"{request.run_name}.log"
    command = _command(request)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(  # noqa: S603 - arguments are validated above and never passed through a shell
            command, cwd=REPO_ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env
        )
    _job = _Job(request.run_name, process, time.time(), total, log_path, _display_command(command))
    logger.info("evaluation run %s started (pid %s, %d questions)", request.run_name, process.pid, total)
    return _job_status()


@router.post("/eval/stop", responses=ERRORS)
def stop_run() -> dict:
    """Stop the run in progress. What it finished so far stays on disk and can be resumed."""
    job = _job
    if job is None or job.process.poll() is not None:
        raise HTTPException(status_code=409, detail="No evaluation run is in progress")
    job.stopped = True
    job.process.terminate()
    try:
        job.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        job.process.kill()
    logger.info("evaluation run %s stopped", job.run_name)
    return _job_status()
