import pytest

from app.ingestion.indexer import index_chunks
from app.query import retrieval
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
