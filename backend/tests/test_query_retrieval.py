import asyncio
import threading

import pytest

from app.query import retrieval
from app.query.retrieval import RetrievalHit, retrieve
from app.shared.keyword_index import KeywordIndex
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import VectorStore

QUERY_VECTORS = {
    "revenue": [1.0, 0.0, 0.0],
    "income": [0.9, 0.1, 0.0],
}


def _chunk(chunk_id: str, text: str, scope: str = "persistent") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        source_doc=f"{chunk_id}.pdf",
        page=1,
        chunk_type="text",
        extraction_method="text",
        corpus_scope=scope,
    )


@pytest.fixture
def stores(tmp_path):
    store = VectorStore(path=str(tmp_path / "retrieval"))
    store.upsert(
        [
            (_chunk("near", "revenue grew strongly"), [1.0, 0.0, 0.0]),
            (_chunk("far", "unrelated appendix"), [0.0, 1.0, 0.0]),
            (_chunk("leak", "revenue in a session upload", "session:abc"), [1.0, 0.0, 0.0]),
        ]
    )
    yield store, KeywordIndex.rebuild_from(store)
    store.close()


@pytest.fixture
def embed_calls(monkeypatch):
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [QUERY_VECTORS.get(text, [0.0, 0.0, 1.0]) for text in texts]

    monkeypatch.setattr(retrieval, "embed_queries", fake_embed)
    return calls


def _retrieve(stores, queries=("revenue", "income"), question="revenue", scope="persistent"):
    store, index = stores
    return asyncio.run(
        retrieve(
            question,
            list(queries),
            vector_store=store,
            keyword_index=index,
            corpus_scope=scope,
            top_k=10,
        )
    )


def test_vector_and_keyword_hits_are_separate_lists(stores, embed_calls):
    result = _retrieve(stores)

    assert result.vector_hits and result.keyword_hits
    assert {h.retriever for h in result.vector_hits} == {"vector"}
    assert {h.retriever for h in result.keyword_hits} == {"keyword"}


def test_vector_hits_carry_the_query_variant_and_keyword_hits_do_not(stores, embed_calls):
    result = _retrieve(stores)

    assert {h.query for h in result.vector_hits} == {"revenue", "income"}
    assert all(h.query is None for h in result.keyword_hits)


def test_hit_fields(stores, embed_calls):
    top = _retrieve(stores).vector_hits[0]

    assert isinstance(top, RetrievalHit)
    assert top.chunk_id == "near"
    assert top.text == "revenue grew strongly"
    assert top.metadata["source_doc"] == "near.pdf"
    assert top.metadata["page"] == 1
    assert "text" not in top.metadata and "chunk_id" not in top.metadata
    assert top.score == pytest.approx(1.0)


def test_scope_filter_applies_to_both_retrievers(stores, embed_calls):
    result = _retrieve(stores)

    assert "leak" not in {h.chunk_id for h in result.vector_hits + result.keyword_hits}
    assert {h.metadata["corpus_scope"] for h in result.vector_hits + result.keyword_hits} == {"persistent"}


def test_scope_filter_selects_the_requested_scope(stores, embed_calls):
    result = _retrieve(stores, scope="session:abc")

    assert {h.chunk_id for h in result.vector_hits} == {"leak"}
    assert {h.chunk_id for h in result.keyword_hits} == {"leak"}


def test_keyword_search_uses_resolved_question_not_expansions(stores, embed_calls):
    result = _retrieve(stores, queries=["appendix"], question="revenue")

    assert {h.chunk_id for h in result.keyword_hits} == {"near"}


def test_all_queries_embedded_in_one_batched_call(stores, embed_calls):
    _retrieve(stores, queries=["revenue", "income", "sales"])

    assert embed_calls == [["revenue", "income", "sales"]]


def test_no_matches_yields_empty_lists(stores, embed_calls):
    result = _retrieve(stores, question="zzzz")

    assert result.keyword_hits == []


def test_vector_and_keyword_searches_run_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)

    def embed(texts):
        barrier.wait()
        return [[1.0, 0.0, 0.0] for _ in texts]

    class Store:
        def search(self, vector, k, where=None):
            return []

        def get(self, chunk_ids):
            return []

    class Index:
        def search(self, query, k, corpus_scope=None):
            barrier.wait()
            return []

    monkeypatch.setattr(retrieval, "embed_queries", embed)

    result = asyncio.run(
        retrieve(
            "q",
            ["q"],
            vector_store=Store(),
            keyword_index=Index(),
            corpus_scope="persistent",
            top_k=5,
        )
    )

    assert result.vector_hits == [] and result.keyword_hits == []
    assert not barrier.broken
