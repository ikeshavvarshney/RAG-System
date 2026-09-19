import pytest

from app.ingestion.indexer import index_chunks
from app.query import cache, retrieval
from app.shared.schemas.citation import CorpusCitation
from app.shared.schemas.chunk import Chunk


@pytest.fixture
def corpus(monkeypatch):
    index_chunks(
        [
            Chunk(
                chunk_id="c1",
                text="quarterly revenue grew strongly",
                source_doc="report.pdf",
                page=3,
                chunk_type="text",
                extraction_method="text",
                corpus_scope="persistent",
            )
        ]
    )
    monkeypatch.setattr(retrieval, "embed_queries", lambda texts: [[0.5] * 8 for _ in texts])


def test_empty_question_returns_guardrail_result(client, fake_llm):
    response = client.post("/api/query", json={"question": ""})

    assert response.status_code == 200
    body = response.json()
    assert body["terminated_at"] == "guardrail"
    assert body["response"]


def test_greeting_short_circuits(client, fake_llm):
    body = client.post("/api/query", json={"question": "hi"}).json()

    assert fake_llm.calls == []
    assert body["terminated_at"] == "greeting"
    assert body["retrieval"] == {"vector_hits": [], "keyword_hits": []}


def test_question_returns_both_hit_lists_and_ignores_session_id(client, fake_llm, corpus):
    fake_llm.replies["query_guardrail"] = '{"safe": true}'
    fake_llm.replies["query_expansion"] = '["sales growth"]'

    response = client.post(
        "/api/query",
        json={"question": "how did revenue grow?", "session_id": "0" * 32},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["terminated_at"] == "retrieved"
    assert body["raw_question"] == "how did revenue grow?"
    assert body["resolved_question"] == "how did revenue grow?"
    assert body["expanded_queries"] == ["how did revenue grow?", "sales growth"]
    assert [h["chunk_id"] for h in body["retrieval"]["keyword_hits"]] == ["c1"]
    assert {h["retriever"] for h in body["retrieval"]["vector_hits"]} == {"vector"}


def test_missing_question_is_rejected_by_schema(client):
    assert client.post("/api/query", json={}).status_code == 422


def test_session_id_is_generated_when_absent_and_returned(client, fake_llm):
    first = client.post("/api/query", json={"question": ""}).json()
    second = client.post("/api/query", json={"question": ""}).json()

    assert len(first["session_id"]) == 32
    assert first["session_id"] != second["session_id"]


def test_supplied_session_id_is_echoed(client, fake_llm):
    session_id = "ab" * 16

    body = client.post("/api/query", json={"question": "", "session_id": session_id}).json()

    assert body["session_id"] == session_id


def test_malformed_session_id_is_rejected(client, fake_llm):
    response = client.post("/api/query", json={"question": "hi", "session_id": "../etc"})

    assert response.status_code == 400


def test_session_id_drives_history_across_requests(client, fake_llm, corpus):
    fake_llm.replies["query_guardrail"] = '{"safe": true}'
    fake_llm.replies["query_greeting"] = '{"kind": "other"}'
    fake_llm.replies["query_expansion"] = "[]"
    fake_llm.replies["query_history"] = "Why did quarterly revenue grow strongly?"
    session_id = "cd" * 16

    first = client.post("/api/query", json={"question": "how did quarterly revenue grow?", "session_id": session_id}).json()
    second = client.post("/api/query", json={"question": "why did it?", "session_id": session_id}).json()
    other = client.post("/api/query", json={"question": "why did it?", "session_id": "ef" * 16}).json()

    assert first["resolved_question"] == "how did quarterly revenue grow?"
    assert second["resolved_question"] == "Why did quarterly revenue grow strongly?"
    assert other["resolved_question"] == "why did it?"


def test_cache_hit_returns_answer_and_citations(client, fake_llm, monkeypatch):
    monkeypatch.setattr(cache, "embed_queries", lambda texts: [[0.5] * 8 for _ in texts])
    cache.put(
        "raw",
        "what was the funding request?",
        "It was $822 million.",
        [CorpusCitation(source_doc="nasa.pdf", page=1, chunk_id="c9")],
        "persistent",
    )
    fake_llm.replies["query_guardrail"] = '{"safe": true}'

    body = client.post("/api/query", json={"question": "what was the funding request?"}).json()

    assert body["terminated_at"] == "cache_hit"
    assert body["answer"] == "It was $822 million."
    assert body["citations"] == [
        {"kind": "corpus", "source_doc": "nasa.pdf", "page": 1, "chunk_id": "c9"}
    ]
