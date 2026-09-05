import uuid

import pytest

from app.core.config import settings
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import SearchResult, VectorStore


def _chunk(
    text: str = "some text",
    *,
    source_doc: str = "a.pdf",
    corpus_scope: str = "persistent",
    extraction_method: str = "text",
    chunk_type: str = "text",
    page: int | None = 1,
    location: str | None = "section 1",
) -> Chunk:
    return Chunk(
        chunk_id=str(uuid.uuid4()),
        text=text,
        source_doc=source_doc,
        page=page,
        location=location,
        chunk_type=chunk_type,
        extraction_method=extraction_method,
        corpus_scope=corpus_scope,
    )


def _vec(seed: float, dim: int = 8) -> list[float]:
    return [seed + i * 0.01 for i in range(dim)]


@pytest.fixture
def chroma_path(tmp_path, monkeypatch):
    path = str(tmp_path / "chroma")
    monkeypatch.setattr(settings, "CHROMA_PATH", path)
    return path


@pytest.fixture
def store(chroma_path):
    return VectorStore()


def test_empty_operations(store):
    assert store.upsert([]) == []
    assert store.get([]) == []
    assert store.count() == 0


def test_vectors_persist_across_fresh_client(chroma_path):
    first = VectorStore()
    chunk = _chunk("persist me")
    first.upsert([(chunk, _vec(0.1))])
    assert first.count() == 1

    # Fresh instance pointed at the same path -> simulates a process restart.
    second = VectorStore()
    assert second.count() == 1
    reloaded = second.get([chunk.chunk_id])
    assert len(reloaded) == 1
    assert reloaded[0].chunk_id == chunk.chunk_id
    assert reloaded[0].text == "persist me"


def test_reupsert_same_id_is_idempotent(store):
    chunk = _chunk("version one")
    store.upsert([(chunk, _vec(0.2))])
    assert store.count() == 1

    updated = chunk.model_copy(update={"text": "version two"})
    store.upsert([(updated, _vec(0.9))])

    assert store.count() == 1  # updated, not duplicated
    assert store.get([chunk.chunk_id])[0].text == "version two"


def test_upsert_populates_dense_vector_id(store):
    chunk = _chunk("x")
    assert chunk.dense_vector_id is None

    (returned,) = store.upsert([(chunk, _vec(0.7))])

    assert returned.dense_vector_id == chunk.chunk_id
    assert store.get([chunk.chunk_id])[0].dense_vector_id == chunk.chunk_id


def test_search_filters_by_corpus_scope(store):
    persistent = _chunk("persistent doc", source_doc="p.pdf", corpus_scope="persistent")
    session = _chunk("session doc", source_doc="s.pdf", corpus_scope="session")
    store.upsert([(persistent, _vec(0.5)), (session, _vec(0.5))])

    hits = store.search(_vec(0.5), k=10, where={"corpus_scope": "persistent"})

    ids = {h.chunk_id for h in hits}
    assert persistent.chunk_id in ids
    assert session.chunk_id not in ids
    assert all(h.chunk.corpus_scope == "persistent" for h in hits)


def test_search_filters_by_extraction_method_vision(store):
    v1 = _chunk("chart a", extraction_method="vision", chunk_type="chart")
    v2 = _chunk("chart b", extraction_method="vision", chunk_type="chart")
    plain = _chunk("plain text", extraction_method="text")
    ocr = _chunk("ocr text", extraction_method="ocr")
    store.upsert(
        [(v1, _vec(0.30)), (v2, _vec(0.31)), (plain, _vec(0.32)), (ocr, _vec(0.33))]
    )

    hits = store.search(_vec(0.30), k=10, where={"extraction_method": "vision"})

    assert {h.chunk_id for h in hits} == {v1.chunk_id, v2.chunk_id}
    assert all(h.chunk.extraction_method == "vision" for h in hits)


def test_delete_by_document_removes_matches_and_returns_ids(store):
    a1 = _chunk("a p1", source_doc="a.pdf", corpus_scope="persistent", page=1)
    a2 = _chunk("a p2", source_doc="a.pdf", corpus_scope="persistent", page=2)
    a_session = _chunk("a session", source_doc="a.pdf", corpus_scope="session")
    b1 = _chunk("b p1", source_doc="b.pdf", corpus_scope="persistent")
    store.upsert(
        [(a1, _vec(0.1)), (a2, _vec(0.2)), (a_session, _vec(0.3)), (b1, _vec(0.4))]
    )
    assert store.count() == 4

    deleted = store.delete_by_document("a.pdf", "persistent")

    assert set(deleted) == {a1.chunk_id, a2.chunk_id}
    assert store.count() == 2

    remaining = {h.chunk_id for h in store.search(_vec(0.1), k=10)}
    assert a1.chunk_id not in remaining
    assert a2.chunk_id not in remaining
    assert a_session.chunk_id in remaining
    assert b1.chunk_id in remaining


def test_delete_by_document_no_match_returns_empty(store):
    store.upsert([(_chunk("only doc", source_doc="a.pdf"), _vec(0.1))])

    assert store.delete_by_document("missing.pdf", "persistent") == []
    assert store.count() == 1


def test_none_optional_metadata_roundtrips_as_none(store):
    pageless = _chunk("no page no loc", page=None, location=None)
    store.upsert([(pageless, _vec(0.6))])

    got = store.get([pageless.chunk_id])[0]
    assert got.page is None
    assert got.location is None

    # A filter on page must not match the page-less chunk.
    with_page = _chunk("has page", page=5)
    store.upsert([(with_page, _vec(0.6))])
    hits = store.search(_vec(0.6), k=10, where={"page": 5})
    assert {h.chunk_id for h in hits} == {with_page.chunk_id}


def test_search_results_carry_full_chunk_text_and_metadata(store):
    chunk = _chunk("the quick brown fox", source_doc="doc.pdf", page=3, location="Intro")
    store.upsert([(chunk, _vec(0.4))])

    (hit,) = store.search(_vec(0.4), k=1)

    assert isinstance(hit, SearchResult)
    assert hit.chunk_id == chunk.chunk_id
    assert isinstance(hit.distance, float)
    assert hit.chunk.text == "the quick brown fox"
    assert hit.chunk.source_doc == "doc.pdf"
    assert hit.chunk.page == 3
    assert hit.chunk.location == "Intro"
    assert hit.chunk.dense_vector_id == chunk.chunk_id


def test_search_k_limits_results(store):
    store.upsert([(_chunk(f"chunk {i}"), _vec(0.5 + i * 0.001)) for i in range(10)])

    assert len(store.search(_vec(0.5), k=3)) == 3
