"""The HTTP contract: response schemas, the SSE stream, cancellation and error bodies. No network."""

import asyncio
import io
import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.api.schemas import QueryResponse
from app.api.streaming import stream_query
from app.ingestion.indexer import index_chunks
from app.main import create_app
from app.query import llm
from app.query.guardrails.output import SafetyVerdict
from app.query.pipeline import QueryResult, StageEvent
from app.shared.schemas.chunk import Chunk
from app.shared.schemas.citation import CorpusCitation, WebCitation

SESSION = "ab" * 16
ANSWER_TEXT = "UNIQUE-ANSWER-TEXT-7731 is the verified answer [1]."


def _result(citations, answer=ANSWER_TEXT) -> QueryResult:
    return QueryResult(
        "retrieved", "raw q", "resolved q", answer=answer, citations=citations, safety=SafetyVerdict(
            verdict="pass", reason="ok", source="llm"
        ),
    )


def _fake_pipeline(monkeypatch, citations, answer=ANSWER_TEXT):
    """Replace run_query with one that emits stage events and records usage from a worker thread."""

    async def fake_run_query(question, session_id, on_event=None):
        for stage in ("retrieval", "generation"):
            on_event(StageEvent(stage, "started"))
            await asyncio.to_thread(llm._client.tracker.record, f"query_{stage}", "m", 10, 5)
            on_event(StageEvent(stage, "completed", 12.5))
        return _result(citations, answer)

    monkeypatch.setattr("app.chains.run_query", fake_run_query)


def _frames(response) -> list[tuple[str, dict]]:
    frames = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        frames.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return frames


CORPUS = [CorpusCitation(source_doc="a.pdf", page=2, chunk_id="c1", score=0.9, snippet="s")]
WEB = [WebCitation(source_url="https://example.com/x", title="X", score=0.4, snippet="w")]


@pytest.mark.parametrize("citations,kinds", [(CORPUS, ["corpus"]), (WEB, ["web"]), (CORPUS + WEB, ["corpus", "web"])])
def test_query_response_matches_the_schema_for_each_citation_type(client, monkeypatch, citations, kinds):
    _fake_pipeline(monkeypatch, citations)

    response = client.post("/api/query", json={"question": "q", "session_id": SESSION})

    assert response.status_code == 200
    body = QueryResponse.model_validate(response.json())
    assert [c.kind for c in body.citations] == kinds
    assert body.answer == ANSWER_TEXT and body.session_id == SESSION
    assert body.raw_question == "raw q" and body.resolved_question == "resolved q"
    assert body.cache_hit is False and body.decomposed is False and body.sub_questions is None
    assert [(s.stage, s.status) for s in body.stages] == [("retrieval", "completed"), ("generation", "completed")]
    assert body.usage.total_tokens == 30 and body.usage.by_stage["query_generation"].output_tokens == 5
    assert body.safety is not None and body.safety.verdict == "pass"
    raw = response.json()["citations"]
    assert [c["kind"] for c in raw] == kinds
    if "web" in kinds:
        assert any(c.get("source_url") for c in raw)


def test_stream_emits_ordered_stage_usage_and_terminal_result(client, monkeypatch):
    _fake_pipeline(monkeypatch, CORPUS)

    with client.stream("POST", "/api/query/stream", json={"question": "q", "session_id": SESSION}) as response:
        response.read()
    frames = _frames(response)

    names = [name for name, _ in frames]
    assert names == ["stage", "usage", "stage", "stage", "usage", "stage", "result"]
    stages = [(p["stage"], p["status"]) for n, p in frames if n == "stage"]
    assert stages == [
        ("retrieval", "started"), ("retrieval", "completed"), ("generation", "started"), ("generation", "completed")
    ]
    assert frames[2][1]["duration_ms"] == 12.5 and "duration_ms" not in frames[0][1]
    usage = [p for n, p in frames if n == "usage"]
    assert usage[0] == {
        "stage": "query_retrieval", "model": "m", "prompt_tokens": 10, "output_tokens": 5, "total_tokens": 15
    }
    final = QueryResponse.model_validate(frames[-1][1])
    assert final.usage.total_tokens == 30 and final.citations[0].kind == "corpus"


def test_answer_text_never_appears_before_the_terminal_event(client, monkeypatch):
    _fake_pipeline(monkeypatch, CORPUS)

    with client.stream("POST", "/api/query/stream", json={"question": "q"}) as response:
        response.read()
    frames = _frames(response)

    assert frames[-1][0] == "result"
    assert all("UNIQUE-ANSWER-TEXT-7731" not in json.dumps(payload) for _, payload in frames[:-1])
    assert "UNIQUE-ANSWER-TEXT-7731" in json.dumps(frames[-1][1])


def test_stream_has_proxy_safe_headers(client, monkeypatch):
    _fake_pipeline(monkeypatch, CORPUS)

    with client.stream("POST", "/api/query/stream", json={"question": "q"}) as response:
        response.read()

    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-type"].startswith("text/event-stream")


def test_closing_the_stream_cancels_the_pipeline(monkeypatch):
    state = {"started": asyncio.Event(), "cancelled": False}

    async def hanging_run_query(question, session_id, on_event=None):
        on_event(StageEvent("retrieval", "started"))
        state["started"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr("app.chains.run_query", hanging_run_query)

    async def scenario():
        frames = stream_query("q", SESSION)
        first = await frames.__anext__()
        await state["started"].wait()
        await frames.aclose()  # what the server does when the client disconnects
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return first, leftover

    first, leftover = asyncio.run(scenario())

    assert first.startswith("event: stage")
    assert state["cancelled"] is True
    assert leftover == []


def test_pipeline_failure_becomes_a_final_error_event_without_internals(client, monkeypatch):
    async def failing(question, session_id, on_event=None):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.chains.run_query", failing)

    with client.stream("POST", "/api/query/stream", json={"question": "q"}) as response:
        response.read()
    frames = _frames(response)

    assert [n for n, _ in frames] == ["error"]
    assert frames[0][1] == {"code": "internal_error", "message": "The query could not be completed."}
    assert "secret internal detail" not in response.text


def test_error_bodies_share_one_shape(monkeypatch):
    client = TestClient(create_app(), raise_server_exceptions=False)

    bad_session = client.post("/api/query", json={"question": "q", "session_id": "../x"})
    bad_body = client.post("/api/query", json={})
    stream_bad_session = client.post("/api/query/stream", json={"question": "q", "session_id": "../x"})
    missing_doc = client.delete("/api/documents/nope.pdf")
    no_route = client.get("/api/nothing-here")

    async def boom(question, session_id, on_event=None):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("app.chains.run_query", boom)
    crashed = client.post("/api/query", json={"question": "q"})

    expected = [
        (bad_session, 400, "bad_request"),
        (bad_body, 422, "validation_error"),
        (stream_bad_session, 400, "bad_request"),
        (missing_doc, 404, "not_found"),
        (no_route, 404, "not_found"),
        (crashed, 500, "internal_error"),
    ]
    for response, status, code in expected:
        assert response.status_code == status
        assert set(response.json()) == {"error"}
        assert response.json()["error"]["code"] == code and response.json()["error"]["message"]
    assert bad_body.json()["error"]["fields"] == [{"field": "question", "message": "Field required"}]
    assert "secret internal detail" not in crashed.text and "Traceback" not in crashed.text


def test_openapi_builds_with_the_real_schemas(client):
    spec = client.get("/openapi.json")

    assert spec.status_code == 200
    document = spec.json()
    for method, path in [
        ("get", "/api/health"),
        ("post", "/api/ingest"),
        ("post", "/api/session/upload"),
        ("get", "/api/documents"),
        ("delete", "/api/documents/{source_doc}"),
        ("post", "/api/query"),
        ("post", "/api/query/stream"),
    ]:
        assert method in document["paths"][path], (method, path)
    schemas = document["components"]["schemas"]
    assert {"QueryResponse", "CorpusCitation", "WebCitation", "UsageSummary", "ErrorResponse"} <= set(schemas)
    assert schemas["QueryResponse"]["properties"]["citations"]["items"]["discriminator"]["propertyName"] == "kind"
    stream = document["paths"]["/api/query/stream"]["post"]["responses"]["200"]
    assert "text/event-stream" in stream["content"]
    assert client.get("/docs").status_code == 200


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_session_upload_and_documents_endpoints(client):
    session_id = client.post("/api/session").json()["session_id"]

    uploaded = client.post(
        "/api/session/upload",
        data={"session_id": session_id},
        files={"files": ("mine.pdf", io.BytesIO(_pdf("A readable page of text for the session upload.")), "application/pdf")},
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["succeeded"] == ["mine.pdf"] and uploaded.json()["corpus_scope"] == f"session:{session_id}"
    assert client.post("/api/session/upload", files={"files": ("x.pdf", io.BytesIO(b""))}).status_code == 422


def test_corpus_documents_can_be_listed_and_deleted(client, monkeypatch):
    index_chunks(
        [
            Chunk(
                chunk_id="c1", text="quarterly revenue grew", source_doc="report.pdf", page=1,
                chunk_type="text", extraction_method="text", corpus_scope="persistent",
            )
        ]
    )

    listed = client.get("/api/documents").json()
    deleted = client.delete("/api/documents/report.pdf")
    after = client.get("/api/documents").json()

    assert [d["source_doc"] for d in listed["documents"]] == ["report.pdf"]
    assert deleted.status_code == 200 and deleted.json()["deleted_chunks"] == 1
    assert after == {"documents": []}
