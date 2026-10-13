"""The evaluation dashboard API: reading run results and starting a run. No child process is ever spawned."""

import json

import pytest

from app.api.routes import evaluation

REFERENCE = [
    {"id": "Q01", "category": "single_hop", "question": "q1", "reference_answer": "a1", "visual_dependent": False},
    {"id": "Q02", "category": "table_chart", "question": "q2", "reference_answer": "a2", "visual_dependent": True},
    {"id": "Q03", "category": "single_hop", "question": "q3", "reference_answer": "a3", "visual_dependent": False},
]
SCORES = {"faithfulness": 1.0, "answer_relevancy": 0.9, "context_precision": 0.5, "context_recall": 1.0}


class _FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = 4242
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture(autouse=True)
def eval_dirs(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    reference = tmp_path / "reference_set.json"
    reference.write_text(json.dumps(REFERENCE), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("GROQ_EVAL_API_KEYS=key-one,key-two\n", encoding="utf-8")
    monkeypatch.setattr(evaluation, "RESULTS_DIR", results)
    monkeypatch.setattr(evaluation, "REFERENCE_PATH", reference)
    monkeypatch.setattr(evaluation, "ENV_PATH", env)
    monkeypatch.setattr(evaluation, "_job", None)
    monkeypatch.delenv("GROQ_EVAL_API_KEYS", raising=False)
    return results


@pytest.fixture
def spawned(monkeypatch):
    """Capture the command instead of running it."""
    calls = []

    def _popen(command, **kwargs):
        process = _FakeProcess()
        calls.append({"command": command, "kwargs": kwargs, "process": process})
        return process

    monkeypatch.setattr(evaluation.subprocess, "Popen", _popen)
    return calls


def _raw_question(question_id, answer="the answer", error=None):
    return {
        "id": question_id, "category": "single_hop", "question": f"question {question_id}",
        "ground_truth": "the reference", "answer": answer, "answer_source": "corpus", "error": error,
        "contexts": ["c1", "c2"], "citations": [{"kind": "corpus"}], "wall_clock_sec": 12.5,
        "requires_tables_charts": False, "terminated_at": "retrieved",
    }


def _write_run(results, name, ids=("Q01", "Q02"), scored=True):
    (results / f"{name}.raw.json").write_text(
        json.dumps({"run_name": name, "config": {"preset": None}, "questions": [_raw_question(i) for i in ids]}),
        encoding="utf-8",
    )
    rows = [{"id": i, "status": "ok", "scores": SCORES, "failures": [], "prompt_tokens": 100,
             "completion_tokens": 20, "judge_calls": 12, "seconds": 3.0} for i in ids]
    lines = [json.dumps({"_config": {"preset": None}})] + [json.dumps(row) for row in rows]
    (results / f"{name}.scores.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if scored:
        (results / f"{name}.scored.json").write_text(
            json.dumps({
                "run_name": name, "judge_model": "judge-model", "config": {"preset": None, "effective": {"RRF_K": 60}},
                "split": {"questions": len(ids), "corpus_coverage_percent": 100.0},
                "aggregate": {"overall": {"questions": len(ids), **SCORES}},
                "judge_usage": {"prompt_tokens": 200, "completion_tokens": 40, "http_429": 0, "scoring_seconds": 6.0},
                "per_question": rows, "skipped": [],
            }),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# Reading results
# --------------------------------------------------------------------------- #
def test_reference_summary_counts_categories_and_reports_the_judge_key(client):
    body = client.get("/api/eval/reference").json()

    assert body["questions"] == 3
    assert body["per_category"] == {"single_hop": 2, "table_chart": 1}
    assert body["visual_dependent"] == 1
    assert body["ids"] == ["Q01", "Q02", "Q03"]
    assert body["judge_configured"] is True


def test_a_placeholder_judge_key_counts_as_not_configured(client):
    evaluation.ENV_PATH.write_text("GROQ_EVAL_API_KEYS=PASTE_REAL_KEY_HERE\n", encoding="utf-8")

    assert client.get("/api/eval/reference").json()["judge_configured"] is False


def test_no_results_directory_lists_no_runs(client, monkeypatch, tmp_path):
    monkeypatch.setattr(evaluation, "RESULTS_DIR", tmp_path / "missing")

    assert client.get("/api/eval/runs").json() == {"runs": []}


def test_runs_are_listed_with_their_scores_and_status(client, eval_dirs):
    _write_run(eval_dirs, "baseline")
    _write_run(eval_dirs, "half-done", scored=False)

    runs = {run["name"]: run for run in client.get("/api/eval/runs").json()["runs"]}

    assert runs["baseline"]["status"] == "scored"
    assert runs["baseline"]["scores"]["context_precision"] == 0.5
    assert runs["baseline"]["judge_model"] == "judge-model"
    assert runs["half-done"]["status"] == "partial"
    assert runs["half-done"]["scores"]["faithfulness"] is None


def test_run_detail_joins_each_question_with_its_scores(client, eval_dirs):
    _write_run(eval_dirs, "baseline")

    body = client.get("/api/eval/runs/baseline").json()

    assert body["status"] == "scored"
    assert body["aggregate"]["overall"]["faithfulness"] == 1.0
    first = body["questions"][0]
    assert first["question"] == "question Q01"
    assert first["reference_answer"] == "the reference"
    assert first["answer"] == "the answer"
    assert first["scores"] == SCORES
    assert first["context_count"] == 2
    assert first["judge_tokens"] == 120


def test_an_unscored_run_still_returns_its_questions(client, eval_dirs):
    (eval_dirs / "fresh.raw.json").write_text(
        json.dumps({"config": None, "questions": [_raw_question("Q01")]}), encoding="utf-8"
    )

    body = client.get("/api/eval/runs/fresh").json()

    assert body["status"] == "partial"
    assert body["aggregate"] is None
    assert body["questions"][0]["status"] == "unscored"
    assert body["questions"][0]["scores"]["faithfulness"] is None


def test_an_unknown_run_is_not_found(client):
    response = client.get("/api/eval/runs/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Starting a run
# --------------------------------------------------------------------------- #
def test_status_is_idle_before_any_run(client):
    assert client.get("/api/eval/status").json() == {"state": "idle"}


def test_starting_a_run_builds_the_harness_command(client, spawned):
    response = client.post(
        "/api/eval/runs", json={"run_name": "sweep-a", "limit": 2, "dense_weight": 0.75, "preset": "naive"}
    )

    assert response.status_code == 202
    command = spawned[0]["command"]
    assert command[1].endswith("run_eval.py")
    assert command[2:] == ["--run-name", "sweep-a", "--limit", "2", "--preset", "naive", "--set", "FUSION_DENSE_WEIGHT=0.75"]
    assert "shell" not in spawned[0]["kwargs"]
    body = response.json()
    assert body["state"] == "running"
    assert body["phase"] == "answering"
    assert body["total"] == 2
    assert body["command"] == "python eval/run_eval.py --run-name sweep-a --limit 2 --preset naive --set FUSION_DENSE_WEIGHT=0.75"


def test_selected_ids_set_the_total_and_reach_the_command(client, spawned):
    body = client.post("/api/eval/runs", json={"run_name": "picked", "ids": ["Q03", "Q01", "Q99"]}).json()

    assert body["total"] == 2
    assert spawned[0]["command"][-2:] == ["--ids", "Q03,Q01,Q99"]


def test_progress_follows_the_files_the_harness_writes(client, spawned, eval_dirs):
    client.post("/api/eval/runs", json={"run_name": "live"})
    _write_run(eval_dirs, "live", ids=("Q01", "Q02", "Q03"), scored=False)
    (eval_dirs / "live.scores.jsonl").write_text(
        json.dumps({"_config": {}}) + "\n" + json.dumps({"id": "Q01", "status": "ok", "scores": SCORES}) + "\n",
        encoding="utf-8",
    )
    (eval_dirs / "live.log").write_text("effective config: {}\n\n[Q01] ok corpus 3.0s\n", encoding="utf-8")

    body = client.get("/api/eval/status").json()

    assert (body["answered"], body["scored"], body["total"]) == (3, 1, 3)
    assert body["phase"] == "scoring"
    assert body["log"] == ["effective config: {}", "[Q01] ok corpus 3.0s"]


def test_a_finished_run_reports_what_its_exit_code_means(client, spawned):
    client.post("/api/eval/runs", json={"run_name": "cached"})
    spawned[0]["process"].returncode = 3

    body = client.get("/api/eval/status").json()

    assert body["state"] == "failed"
    assert body["exit_code"] == 3
    assert "answer cache" in body["message"]


def test_a_second_run_is_refused_while_one_is_in_progress(client, spawned):
    client.post("/api/eval/runs", json={"run_name": "first"})

    response = client.post("/api/eval/runs", json={"run_name": "second"})

    assert response.status_code == 409
    assert len(spawned) == 1


def test_an_existing_run_name_needs_resume(client, spawned, eval_dirs):
    _write_run(eval_dirs, "baseline")

    refused = client.post("/api/eval/runs", json={"run_name": "baseline"})
    resumed = client.post("/api/eval/runs", json={"run_name": "baseline", "resume": True})

    assert refused.status_code == 409
    assert resumed.status_code == 202
    assert spawned[0]["command"][-1] == "--resume"


def test_a_run_is_refused_without_a_judge_key(client, spawned):
    evaluation.ENV_PATH.write_text("", encoding="utf-8")

    response = client.post("/api/eval/runs", json={"run_name": "nokey"})

    assert response.status_code == 409
    assert "GROQ_EVAL_API_KEYS" in response.json()["error"]["message"]
    assert spawned == []


@pytest.mark.parametrize(
    "payload",
    [
        {"run_name": "../escape"},
        {"run_name": "has space"},
        {"run_name": ""},
        {"run_name": "ok", "limit": 0},
        {"run_name": "ok", "dense_weight": 1.5},
        {"run_name": "ok", "preset": "made-up"},
    ],
)
def test_invalid_requests_never_spawn_a_process(client, spawned, payload):
    response = client.post("/api/eval/runs", json=payload)

    assert response.status_code == 422
    assert spawned == []


def test_a_question_id_with_shell_characters_is_rejected(client, spawned):
    response = client.post("/api/eval/runs", json={"run_name": "ok", "ids": ["Q01; rm -rf"]})

    assert response.status_code == 400
    assert spawned == []


def test_a_selection_matching_no_question_is_rejected(client, spawned):
    response = client.post("/api/eval/runs", json={"run_name": "ok", "ids": ["Q99"]})

    assert response.status_code == 400
    assert spawned == []


def test_stopping_a_run_terminates_it_and_keeps_its_name(client, spawned):
    client.post("/api/eval/runs", json={"run_name": "long"})

    body = client.post("/api/eval/stop").json()

    assert spawned[0]["process"].terminated is True
    assert body["state"] == "stopped"
    assert body["run_name"] == "long"


def test_stopping_with_nothing_running_is_a_conflict(client):
    assert client.post("/api/eval/stop").status_code == 409
