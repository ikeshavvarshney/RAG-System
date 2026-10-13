"""RAGAS harness: run questions through the live pipeline, checkpoint raw results, then score one question at a time."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import logging
import os
import re
import sys
import threading
import time
import types
import uuid
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_REFERENCE = REPO_ROOT / "data" / "eval" / "reference_set.json"
MANIFEST = REPO_ROOT / "data" / "corpus" / "manifest.csv"

# Settings reads .env relative to the working directory.
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

warnings.filterwarnings("ignore", category=DeprecationWarning, module="ragas")


def _load_eval_env() -> None:
    env_file = BACKEND / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        name = name.strip()
        if sep and name.startswith("GROQ_EVAL_") and name not in os.environ:
            os.environ[name] = value.strip().strip("\"'")


_load_eval_env()

# ragas 0.4.3 imports this module, which langchain-community 0.4.2 no longer ships.
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = types.ModuleType("langchain_community.chat_models.vertexai")
    _stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules["langchain_community.chat_models.vertexai"] = _stub

_THINK = re.compile(r"<think>.*?</think>", re.S)
UNSCORED_CATEGORIES = {"out_of_corpus"}
MAX_WAIT_SEC = 65.0
MAX_ATTEMPTS = 12
COVERAGE_WARNING = 80.0
ERROR_LIMIT = 0.10
RUN_INVALID_EXIT = 6
METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["questions"] if isinstance(data, dict) else data
    questions = []
    for i, entry in enumerate(entries, start=1):
        sources = entry.get("reference_sources", entry.get("sources", []))
        questions.append(
            {
                "id": entry.get("id") or f"Q{i:02d}",
                "category": entry.get("category"),
                "question": entry["question"],
                "ground_truth": entry.get("ground_truth", entry.get("reference_answer")),
                "reference_sources": [_normalise_source(s) for s in sources],
                "requires_tables_charts": bool(
                    entry.get("requires_tables_charts", entry.get("visual_dependent", False))
                ),
            }
        )
    return questions


def _normalise_source(source: dict) -> dict:
    pages = source.get("pages")
    if pages is None and "page" in source:
        pages = [source["page"]]
    return {"source_doc": source.get("source_doc", source.get("doc")), "pages": pages or []}


def audit_reference_set(path: Path = DEFAULT_REFERENCE) -> dict:
    questions = load_questions(path)
    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        manifest = {row["filename"]: row for row in csv.DictReader(handle)}
    problems = []
    for q in questions:
        for src in q["reference_sources"]:
            row = manifest.get(src["source_doc"])
            if row is None:
                problems.append({"id": q["id"], "source_doc": src["source_doc"], "issue": "not in manifest"})
                continue
            total = int(row["pages"] or 0)
            for page in src["pages"]:
                if not 1 <= page <= total:
                    problems.append(
                        {"id": q["id"], "source_doc": src["source_doc"], "issue": f"page {page} outside 1..{total}"}
                    )
    categories: dict[str, int] = {}
    for q in questions:
        categories[q["category"] or "none"] = categories.get(q["category"] or "none", 0) + 1
    return {
        "entries": len(questions),
        "per_category": categories,
        "visual_dependent": sum(q["requires_tables_charts"] for q in questions),
        "without_sources": [q["id"] for q in questions if not q["reference_sources"]],
        "source_problems": problems,
    }


def collect_contexts(result) -> list[dict]:
    candidates = list(result.context)
    for sub in result.sub_queries:
        candidates.extend(sub.context)
    seen: set[str] = set()
    contexts = []
    for cand in candidates:
        if cand.chunk_id in seen:
            continue
        seen.add(cand.chunk_id)
        meta = cand.metadata
        contexts.append(
            {
                "chunk_id": cand.chunk_id,
                "source": cand.source,
                "text": cand.text,
                "source_doc": meta.get("source_doc"),
                "page": meta.get("page"),
                "source_url": meta.get("source_url"),
                "score": cand.score,
                "retrievers": sorted({p.retriever for p in cand.provenance}),
            }
        )
    return contexts


ALLOWED_KEYS = (
    "FUSION_DENSE_WEIGHT",
    "RRF_K",
    "RETRIEVAL_TOP_K",
    "EXPANSION_COUNT",
    "EXPANSION_ENABLED",
    "DECOMPOSITION_ENABLED",
    "RERANK_ENABLED",
    "RERANK_CANDIDATES",
    "RERANK_TOP_K",
    "CHROMA_PATH",
)
PRESETS = {
    "naive": {
        "FUSION_DENSE_WEIGHT": 1.0,
        "EXPANSION_ENABLED": False,
        "RERANK_ENABLED": False,
        "DECOMPOSITION_ENABLED": False,
        "CHROMA_PATH": "./data/chroma_naive",
    }
}


class ConfigError(ValueError):
    pass


def cast_override(key: str, raw):
    from typing import Annotated

    from pydantic import TypeAdapter, ValidationError

    from app.core.config import Settings

    if key not in ALLOWED_KEYS:
        raise ConfigError(f"unknown config key {key!r}; allowed: {', '.join(ALLOWED_KEYS)}")
    field = Settings.model_fields[key]
    try:
        annotation = Annotated[(field.annotation, *field.metadata)] if field.metadata else field.annotation
        value = TypeAdapter(annotation).validate_python(raw)
    except ValidationError as exc:
        raise ConfigError(f"{key}={raw!r}: {exc.errors()[0]['msg']}") from None
    if key == "CHROMA_PATH":
        value = str((BACKEND / value).resolve())
        if not Path(value).is_dir():
            raise ConfigError(f"CHROMA_PATH {value} does not exist; build that collection first instead of opening an empty one")
    return value


def resolve_overrides(preset: str | None, config_file: Path | None, sets: list[str]) -> dict:
    merged: dict = {}
    if preset:
        if preset not in PRESETS:
            raise ConfigError(f"unknown preset {preset!r}; available: {', '.join(PRESETS)}")
        merged.update(PRESETS[preset])
    if config_file:
        merged.update(json.loads(config_file.read_text(encoding="utf-8")))
    for item in sets:
        key, sep, value = item.partition("=")
        if not sep:
            raise ConfigError(f"--set expects KEY=VALUE, got {item!r}")
        merged[key.strip()] = value.strip()
    return {key: cast_override(key, value) for key, value in merged.items()}


def effective_config(preset: str | None, overrides: dict) -> dict:
    from app.core.config import settings

    return {
        "preset": preset,
        "overrides": overrides,
        "effective": {key: getattr(settings, key) for key in ALLOWED_KEYS},
    }


def _reset_stores() -> None:
    import app.ingestion.indexer as indexer

    if indexer._vector_store is not None:
        indexer._vector_store.close()
    indexer._vector_store = None
    indexer._keyword_index = None


@contextlib.contextmanager
def applied(overrides: dict):
    from app.core.config import settings

    previous = {key: getattr(settings, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(settings, key, value)
        if "CHROMA_PATH" in overrides:
            _reset_stores()
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)
        if "CHROMA_PATH" in overrides:
            _reset_stores()


def config_diff(stored: dict | None, current: dict | None) -> list[str]:
    old = (stored or {}).get("effective", {})
    new = (current or {}).get("effective", {})
    return [
        f"{key}: stored={old.get(key)!r} current={new.get(key)!r}"
        for key in sorted(set(old) | set(new))
        if old.get(key) != new.get(key)
    ]


def answer_source(contexts: list[dict]) -> str:
    kinds = {c["source"] for c in contexts}
    if not kinds:
        return "none"
    if kinds == {"corpus"}:
        return "corpus"
    if kinds == {"web"}:
        return "web"
    return "mixed"


def is_error(record: dict) -> bool:
    return bool(record.get("error")) or record.get("terminated_at") is None


def normalise(records: list[dict]) -> list[dict]:
    for r in records:
        failed = is_error(r)
        r["status"] = "error" if failed else "ok"
        r["answer_source"] = None if failed else r.get("answer_source") or answer_source(r["contexts"])
    return records


def oos_outcome(record: dict) -> str:
    if is_error(record):
        return "error"
    if record["answer_source"] in ("web", "mixed"):
        return "web_fallback"
    if record.get("non_answer") or not record.get("answer"):
        return "declined"
    return "answered_from_corpus"


class _PipelineLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("retrieval config:"):
            self.lines.append(message)


async def run_question(q: dict) -> dict:
    from app.query.pipeline import run_query

    session_id = f"eval-{uuid.uuid4().hex}"
    capture = _PipelineLog()
    pipeline_logger = logging.getLogger("app.query.pipeline")
    pipeline_logger.addHandler(capture)
    began = time.perf_counter()
    record = {**q, "session_id": session_id}
    try:
        result = await run_query(q["question"], session_id, bypass_cache=True)
    except Exception as exc:  # noqa: BLE001 - one failed question must not lose the rest of the run
        record.update(error=f"{type(exc).__name__}: {exc}", terminated_at=None, answer=None, contexts=[], citations=[])
    else:
        subs = result.sub_queries
        record.update(
            error=None,
            terminated_at=result.terminated_at,
            answer=result.answer or result.response,
            contexts=collect_contexts(result),
            citations=[c.model_dump() for c in result.citations],
            non_answer=result.generation.is_non_answer if result.generation else None,
            pipeline={
                "reranked": bool(result.reranked or any(s.reranked for s in subs)),
                "expanded_queries": result.expanded_queries or [e for s in subs for e in s.expanded_queries],
                "sub_questions": len(subs),
            },
        )
    finally:
        pipeline_logger.removeHandler(capture)
    record["retrieval_log"] = capture.lines
    record["wall_clock_sec"] = round(time.perf_counter() - began, 2)
    return normalise([record])[0]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def run_pipeline(
    questions: list[dict], raw_path: Path, run_name: str, finished: dict[str, dict], config: dict
) -> list[dict]:
    records = dict(finished)
    for q in questions:
        if q["id"] in records:
            continue
        record = await run_question(q)
        records[q["id"]] = record
        ordered = [records[x["id"]] for x in questions if x["id"] in records]
        write_json(raw_path, {"run_name": run_name, "config": config, "questions": ordered})
        print(f"[{q['id']}] {record['status']} {record['terminated_at']} {record['answer_source']} {record['wall_clock_sec']}s", flush=True)
        if record["terminated_at"] == "cache_hit":
            break
    return [records[q["id"]] for q in questions if q["id"] in records]


def scorable(record: dict) -> bool:
    return (
        record["category"] not in UNSCORED_CATEGORIES
        and record["terminated_at"] == "retrieved"
        and bool(record["answer"])
        and bool(record["contexts"])
        and bool(record["ground_truth"])
    )


class JudgeStats:
    def __init__(self) -> None:
        self.qid: str | None = None
        self.log_path: Path | None = None
        self.by_question: dict = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "http_429": 0, "errors": []})
        self.lock = threading.Lock()

    def current(self) -> dict:
        return self.by_question[self.qid]


STATS = JudgeStats()


def _seconds(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        pass
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    parts = re.findall(r"([\d.]+)(ms|h|m|s)", text)
    return sum(float(n) * units[u] for n, u in parts) if parts else None


def _reset_seconds(exc) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = headers.get(name)
        seconds = _seconds(value) if value else None
        if seconds is not None:
            return seconds
    return MAX_WAIT_SEC


def build_judge():
    import groq
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import ChatResult
    from langchain_groq import ChatGroq
    from pydantic import PrivateAttr
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from app.core.key_rotation import AllKeysBlocked, KeyRotator

    log = logging.getLogger("eval.judge")
    model = os.environ.get("GROQ_EVAL_MODEL", "openai/gpt-oss-120b")
    rotator = KeyRotator(os.environ.get("GROQ_EVAL_API_KEYS", ""))
    reset_at: dict[str, float] = {}

    def pick_key() -> str:
        while True:
            try:
                return rotator.next("groq")
            except AllKeysBlocked:
                with STATS.lock:
                    wait = min(reset_at.values()) - time.monotonic()
                if wait > MAX_WAIT_SEC:
                    raise RuntimeError(f"all judge keys are rate-limited; earliest reset in {wait:.0f}s") from None
                wait = max(wait, 0.0) + 0.5
                print(f"judge: all {len(rotator)} keys limited, waiting {wait:.0f}s", flush=True)
                time.sleep(wait)

    class RotatingChatGroq(BaseChatModel):
        _clients: dict = PrivateAttr(default_factory=dict)

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            for key in rotator.keys:
                self._clients[key] = ChatGroq(
                    model=model,
                    api_key=key,
                    temperature=0,
                    n=1,
                    max_retries=0,
                    reasoning_effort="low",
                    model_kwargs={"include_reasoning": False},
                )

        @property
        def _llm_type(self) -> str:
            return "rotating-groq"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs,
        ) -> ChatResult:
            for _ in range(MAX_ATTEMPTS):
                key = pick_key()
                began = time.perf_counter()
                try:
                    result = self._clients[key]._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                except groq.RateLimitError as exc:
                    reset = _reset_seconds(exc)
                    with STATS.lock:
                        STATS.current()["http_429"] += 1
                        reset_at[key] = time.monotonic() + reset
                    rotator.block(key, "groq", reset)
                    print(f"judge: 429 on key {rotator.keys.index(key) + 1}, resets in {reset:.0f}s", flush=True)
                    continue
                except Exception as exc:  # noqa: BLE001 - recorded, then ragas decides what the metric becomes
                    with STATS.lock:
                        STATS.current()["errors"].append(f"{type(exc).__name__}: {str(exc)[:200]}")
                    raise
                self._finish(result, key, time.perf_counter() - began)
                return result
            message = f"rate-limited after {MAX_ATTEMPTS} attempts"
            with STATS.lock:
                STATS.current()["errors"].append(message)
            raise RuntimeError(message)

        def _finish(self, result: ChatResult, key: str, seconds: float) -> None:
            prompt = completion = 0
            replies = []
            for generation in result.generations:
                message = generation.message
                message.additional_kwargs.pop("reasoning_content", None)
                if isinstance(message.content, str):
                    message.content = _THINK.sub("", message.content).strip()
                    generation.text = message.content
                replies.append(message.content)
                usage = getattr(message, "usage_metadata", None) or {}
                prompt += usage.get("input_tokens", 0)
                completion += usage.get("output_tokens", 0)
            with STATS.lock:
                entry = STATS.current()
                entry["prompt_tokens"] += prompt
                entry["completion_tokens"] += completion
                entry["calls"] += 1
                if STATS.log_path is not None:
                    append_jsonl(
                        STATS.log_path,
                        {
                            "id": STATS.qid,
                            "key": rotator.keys.index(key) + 1,
                            "prompt_tokens": prompt,
                            "completion_tokens": completion,
                            "seconds": round(seconds, 2),
                            "reply": replies,
                        },
                    )

    class LocalEmbeddings(Embeddings):
        def __init__(self, name: str):
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(name)

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return self._model.encode(texts, normalize_embeddings=True).tolist()

        def embed_query(self, text: str) -> list[float]:
            return self.embed_documents([text])[0]

    llm = LangchainLLMWrapper(RotatingChatGroq())
    embeddings = LangchainEmbeddingsWrapper(
        LocalEmbeddings(os.environ.get("EVAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    )
    return llm, embeddings, model


class _RagasWarnings(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING and STATS.qid is not None:
            with STATS.lock:
                STATS.current()["errors"].append(f"ragas: {record.getMessage()[:200]}")


class Scorer:
    def __init__(self) -> None:
        from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall
        from ragas.run_config import RunConfig

        self.llm, self.embeddings, self.judge_model = build_judge()
        self.run_config = RunConfig(max_workers=1, timeout=600, seed=42)
        self.metrics = {
            "faithfulness": Faithfulness(),
            "answer_relevancy": AnswerRelevancy(strictness=1),
            "context_precision": LLMContextPrecisionWithReference(),
            "context_recall": LLMContextRecall(),
        }
        logging.getLogger("ragas").addHandler(_RagasWarnings())

    def score(self, record: dict) -> dict:
        from ragas import EvaluationDataset, SingleTurnSample, evaluate

        STATS.qid = record["id"]
        STATS.by_question.pop(record["id"], None)
        began = time.perf_counter()
        sample = SingleTurnSample(
            user_input=record["question"],
            response=record["answer"],
            retrieved_contexts=[c["text"] for c in record["contexts"]],
            reference=record["ground_truth"],
        )
        scores: dict[str, float | None] = {name: None for name in self.metrics}
        failures: list[str] = []
        try:
            frame = evaluate(
                EvaluationDataset(samples=[sample]),
                metrics=list(self.metrics.values()),
                llm=self.llm,
                embeddings=self.embeddings,
                run_config=self.run_config,
                raise_exceptions=False,
                show_progress=False,
            ).to_pandas()
            row = frame.iloc[0]
            scores = {name: _number(row.get(metric.name)) for name, metric in self.metrics.items()}
        except Exception as exc:  # noqa: BLE001 - recorded as a failed question, never as a score
            failures.append(f"evaluate raised {type(exc).__name__}: {str(exc)[:200]}")
        stats = STATS.current()
        missing = [name for name, value in scores.items() if value is None]
        if missing:
            reasons = list(dict.fromkeys(stats["errors"]))[:3] or ["metric returned NaN (job failed or timed out)"]
            failures.extend(f"{name}: {'; '.join(reasons)}" for name in missing)
        return {
            "id": record["id"],
            "category": record["category"],
            "status": "failed" if failures else "ok",
            "scores": scores,
            "failures": failures,
            "prompt_tokens": stats["prompt_tokens"],
            "completion_tokens": stats["completion_tokens"],
            "judge_calls": stats["calls"],
            "http_429": stats["http_429"],
            "seconds": round(time.perf_counter() - began, 1),
        }


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else round(number, 4)



SOURCES = ("corpus", "web", "mixed", "none")


def _aggregate(rows: list[dict]) -> dict:
    out: dict = {"questions": len(rows)}
    for name in METRIC_NAMES:
        values = [r["scores"][name] for r in rows if r["scores"].get(name) is not None]
        out[name] = round(sum(values) / len(values), 4) if values else None
        out[f"{name}_n"] = len(values)
    return out


def split_summary(records: list[dict]) -> dict:
    errors = [r for r in records if is_error(r)]
    graded = [r for r in records if r["category"] not in UNSCORED_CATEGORIES and not is_error(r)]
    counts = {s: sum(r["answer_source"] == s for r in graded) for s in SOURCES}
    total = len(graded)
    percent = {s: round(100.0 * counts[s] / total, 1) if total else 0.0 for s in SOURCES}
    return {
        "questions": total,
        "counts": counts,
        "percent": percent,
        "corpus_coverage_percent": percent["corpus"],
        "low_corpus_coverage": bool(total) and percent["corpus"] < COVERAGE_WARNING,
        "errors": len(errors),
        "error_ids": [r["id"] for r in errors],
        "total_questions": len(records),
        "run_invalid": bool(records) and len(errors) / len(records) > ERROR_LIMIT,
    }


def build_scored(
    run_name: str,
    raw_source: str | None,
    judge_model: str,
    config: dict | None,
    records: list[dict],
    rows: list[dict],
    skipped: list[dict],
) -> dict:
    source_of = {r["id"]: r["answer_source"] for r in records}
    rows = [{**r, "answer_source": source_of[r["id"]]} for r in rows]
    ok = [r for r in rows if r["status"] == "ok"]
    errored = [
        {
            "id": r["id"],
            "category": r["category"],
            "status": "error",
            "reason": r.get("error") or "pipeline call failed",
            "answer_source": None,
            "scores": {name: None for name in METRIC_NAMES},
        }
        for r in records
        if is_error(r)
    ]
    by_source = {s: [r for r in ok if r["answer_source"] == s] for s in SOURCES}
    return {
        "run_name": run_name,
        "raw_source": raw_source,
        "judge_model": judge_model,
        "config": config,
        "split": split_summary(records),
        "aggregate": {
            "overall": _aggregate(ok),
            "corpus_only": _aggregate(by_source["corpus"]),
            "web": _aggregate(by_source["web"]),
            "mixed": _aggregate(by_source["mixed"]),
        },
        "failed_questions": sum(r["status"] == "failed" for r in rows),
        "judge_usage": {
            "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["completion_tokens"] for r in rows),
            "http_429": sum(r["http_429"] for r in rows),
            "scoring_seconds": round(sum(r["seconds"] for r in rows), 1),
        },
        "out_of_corpus": [
            {"id": r["id"], "answer_source": r["answer_source"], "outcome": oos_outcome(r)}
            for r in records
            if r["category"] in UNSCORED_CATEGORIES
        ],
        "per_question": rows + errored,
        "errors": [{"id": r["id"], "reason": r["reason"]} for r in errored],
        "failed": [{"id": r["id"], "failures": r["failures"]} for r in rows if r["status"] == "failed"],
        "skipped": skipped,
    }


def print_split(split: dict, records: list[dict] | None = None) -> None:
    ids = f" ({', '.join(split['error_ids'])})" if split["errors"] else ""
    print(f"errors: {split['errors']}{ids}")
    for r in records or []:
        if is_error(r):
            print(f"  {r['id']}: {(r.get('error') or 'pipeline call failed')[:200]}")
    if split["run_invalid"]:
        message = f"RUN INVALID: too many errors ({split['errors']} of {split['total_questions']} questions errored, limit {ERROR_LIMIT:.0%})"
        print(message)
        print(message, file=sys.stderr)
    counts, percent = split["counts"], split["percent"]
    summary = ", ".join(f"{s}={counts[s]} ({percent[s]}%)" for s in SOURCES)
    print(f"ANSWER SOURCE SPLIT ({split['questions']} graded questions, errors excluded): {summary}")
    if split["low_corpus_coverage"]:
        print(f"WARNING: corpus-only coverage {percent['corpus']}% is below {COVERAGE_WARNING:.0f}%; ablation deltas rest on a small subset")


def skip_reason(record: dict) -> str:
    if record["category"] in UNSCORED_CATEGORIES:
        return "out_of_corpus"
    if is_error(record):
        return f"error: {record.get('error') or 'pipeline call failed'}"
    return record.get("error") or (record["terminated_at"] if record["terminated_at"] != "retrieved" else None) or (
        "no answer" if not record["answer"] else "no contexts" if not record["contexts"] else "no reference answer"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--run-name", default=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--limit", type=int, help="first N questions")
    parser.add_argument("--ids", help="comma-separated question ids")
    parser.add_argument("--from-raw", help="score eval/results/<name>.raw.json without running the pipeline")
    parser.add_argument("--resume", action="store_true", help="continue a run that already has partial results")
    parser.add_argument("--no-score", action="store_true", help="run the pipeline and write raw results only")
    parser.add_argument("--preset", help=f"named config preset: {', '.join(PRESETS)}")
    parser.add_argument("--config", type=Path, help="JSON file of config overrides")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="config override, repeatable")
    parser.add_argument("--audit", action="store_true", help="report on the reference set and exit")
    args = parser.parse_args()

    if args.audit:
        print(json.dumps(audit_reference_set(args.reference), indent=2))
        return 0

    if args.from_raw and (args.preset or args.config or args.set):
        print("config overrides cannot be combined with --from-raw; the raw file carries its own config", file=sys.stderr)
        return 2
    try:
        overrides = resolve_overrides(args.preset, args.config, args.set)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    with applied(overrides):
        return run(args, overrides)


def run(args, overrides: dict) -> int:
    from app.core.logging import configure_logging

    configure_logging()
    raw_path = RESULTS_DIR / f"{args.run_name}.raw.json"
    scores_path = RESULTS_DIR / f"{args.run_name}.scores.jsonl"
    scored_path = RESULTS_DIR / f"{args.run_name}.scored.json"
    STATS.log_path = RESULTS_DIR / f"{args.run_name}.judge.jsonl"

    partial = [p.name for p in (scores_path, raw_path) if p.exists()]
    if partial and not args.resume:
        print(f"{partial[0]} already exists; pass --resume to continue this run or choose a new --run-name", file=sys.stderr)
        return 4

    wanted = set(args.ids.split(",")) if args.ids else None
    if args.from_raw:
        raw = json.loads((RESULTS_DIR / f"{args.from_raw}.raw.json").read_text(encoding="utf-8"))
        config = raw.get("config")
        records = [r for r in raw["questions"] if wanted is None or r["id"] in wanted]
        records = records[: args.limit] if args.limit else records
        normalise(records)
    else:
        config = effective_config(args.preset, overrides)
    print("effective config:", json.dumps(config["effective"] if config else None), flush=True)

    if args.resume and (raw_path.exists() or scores_path.exists()):
        if raw_path.exists():
            stored = json.loads(raw_path.read_text(encoding="utf-8")).get("config")
        else:
            first = read_jsonl(scores_path)[:1]
            stored = first[0].get("_config") if first else None
        diff = config_diff(stored, config)
        if diff:
            print("REFUSING to resume: stored config differs from the current one:\n  " + "\n  ".join(diff), file=sys.stderr)
            return 5

    if not args.from_raw:
        questions = [q for q in load_questions(args.reference) if wanted is None or q["id"] in wanted]
        questions = questions[: args.limit] if args.limit else questions
        if not questions:
            print("no questions selected", file=sys.stderr)
            return 2
        finished = {}
        if raw_path.exists():
            stored_records = normalise(json.loads(raw_path.read_text(encoding="utf-8"))["questions"])
            finished = {r["id"]: r for r in stored_records if not is_error(r)}
            retried = [r["id"] for r in stored_records if is_error(r)]
            print(f"resuming pipeline: {len(finished)} of {len(questions)} already run; retrying errored: {retried}")
        records = asyncio.run(run_pipeline(questions, raw_path, args.run_name, finished, config))
        print(f"raw results: {raw_path}")
        hits = [r["id"] for r in records if r["terminated_at"] == "cache_hit"]
        if hits:
            print(f"ABORT: answer cache hit on {hits}; not scoring. Clear CACHE_PATH and rerun.", file=sys.stderr)
            return 3
        if args.no_score:
            split = split_summary(records)
            print_split(split, records)
            return RUN_INVALID_EXIT if split["run_invalid"] else 0

    to_score = [r for r in records if scorable(r)]
    skipped = [{"id": r["id"], "reason": skip_reason(r)} for r in records if not scorable(r)]
    if not scores_path.exists():
        append_jsonl(scores_path, {"_config": config})
    previous = {row["id"]: row for row in read_jsonl(scores_path) if "id" in row}
    done = {i for i, row in previous.items() if row["status"] == "ok"}
    if args.resume:
        print(f"resuming: {len([r for r in to_score if r['id'] in done])} of {len(to_score)} already scored", flush=True)

    judge_model = os.environ.get("GROQ_EVAL_MODEL", "openai/gpt-oss-120b")
    if any(r["id"] not in done for r in to_score):
        scorer = Scorer()
        judge_model = scorer.judge_model
        for record in to_score:
            if record["id"] in done:
                continue
            row = scorer.score(record)
            append_jsonl(scores_path, row)
            previous[record["id"]] = row
            print(
                f"[{row['id']}] {row['status']} {record['answer_source']} {row['seconds']}s "
                f"tokens={row['prompt_tokens']}+{row['completion_tokens']} 429s={row['http_429']} {row['scores']}",
                flush=True,
            )

    rows = [previous[r["id"]] for r in to_score if r["id"] in previous]
    scored = build_scored(args.run_name, args.from_raw, judge_model, config, records, rows, skipped)
    write_json(scored_path, scored)
    print_split(scored["split"], records)
    summary ={"aggregate": scored["aggregate"], "out_of_corpus": scored["out_of_corpus"], "judge_usage": scored["judge_usage"]}
    print(json.dumps(summary, indent=2))
    if scored["failed"]:
        print("FAILED questions:", ", ".join(f"{f['id']} ({'; '.join(f['failures'])[:150]})" for f in scored["failed"]))
    print(f"scored results: {scored_path}")
    if scored["split"]["run_invalid"]:
        return RUN_INVALID_EXIT
    return 1 if scored["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
