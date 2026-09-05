import uuid

import pytest

from app.ingestion import indexer
from app.ingestion.indexer import IndexResult, index_chunks
from app.shared.keyword_index import KeywordIndex
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import VectorStore

# conftest.isolate_index_stores (autouse) already points settings.CHROMA_PATH at
# a temp dir, resets the indexer singletons, and stubs indexer.embed_chunks.


def _chunk_dict(
    text: str,
    *,
    extraction_method: str = "text",
    chunk_type: str = "text",
    source_doc: str = "doc.pdf",
    corpus_scope: str = "persistent",
) -> dict:
    return {
        "chunk_id": str(uuid.uuid4()),
        "text": text,
        "source_doc": source_doc,
        "page": 1,
        "location": None,
        "chunk_type": chunk_type,
        "extraction_method": extraction_method,
        "corpus_scope": corpus_scope,
    }


@pytest.fixture
def stores():
    vector_store = VectorStore()
    return vector_store, KeywordIndex(vector_store)


def test_reports_totals_and_per_extraction_method_counts(stores):
    vector_store, keyword_index = stores
    chunks = [
        _chunk_dict("alpha body text one", extraction_method="text"),
        _chunk_dict("beta body text two", extraction_method="text"),
        _chunk_dict("gamma body text three", extraction_method="text"),
        _chunk_dict("delta scanned page ocr", extraction_method="ocr"),
        _chunk_dict("epsilon scanned page ocr", extraction_method="ocr"),
        _chunk_dict(
            "zeta bar chart 2020 value 10", extraction_method="vision", chunk_type="chart"
        ),
    ]

    result = index_chunks(chunks, vector_store, keyword_index)

    assert isinstance(result, IndexResult)
    assert result.total_indexed == 6
    assert result.by_extraction_method == {"text": 3, "ocr": 2, "vision": 1}
    assert result.vector_store_total == 6
    assert result.keyword_index_total == 6
    assert vector_store.count() == 6
    assert len(keyword_index) == 6


def test_same_chunk_id_set_retrievable_from_both_stores(stores):
    vector_store, keyword_index = stores
    chunks = [_chunk_dict(f"shared body plus marker{i} inside") for i in range(5)]
    expected_ids = {c["chunk_id"] for c in chunks}

    index_chunks(chunks, vector_store, keyword_index)

    assert {c.chunk_id for c in vector_store.all_chunks()} == expected_ids
    for i, chunk in enumerate(chunks):
        hits = keyword_index.search(f"marker{i}", k=3)
        assert hits and hits[0][0] == chunk["chunk_id"]


def test_reindexing_same_chunks_does_not_duplicate(stores):
    vector_store, keyword_index = stores
    chunks = [_chunk_dict(f"stable content number {i}") for i in range(4)]

    first = index_chunks(chunks, vector_store, keyword_index)
    second = index_chunks(chunks, vector_store, keyword_index)

    assert first.total_indexed == 4
    assert second.total_indexed == 4  # re-upserted in place, not new rows
    assert vector_store.count() == 4  # not 8
    assert len(keyword_index) == 4
    assert second.vector_store_total == 4
    assert second.keyword_index_total == 4


def test_empty_input_is_a_noop(stores):
    vector_store, keyword_index = stores

    result = index_chunks([], vector_store, keyword_index)

    assert result.total_indexed == 0
    assert result.by_extraction_method == {"text": 0, "ocr": 0, "vision": 0}
    assert result.vector_store_total == 0
    assert vector_store.count() == 0


def test_accepts_chunk_models_not_only_dicts(stores):
    vector_store, keyword_index = stores
    model = Chunk(
        **_chunk_dict("a chunk passed as a model", extraction_method="vision", chunk_type="chart")
    )

    result = index_chunks([model], vector_store, keyword_index)

    assert result.total_indexed == 1
    assert result.by_extraction_method["vision"] == 1
    assert {c.chunk_id for c in vector_store.all_chunks()} == {model.chunk_id}


def test_bm25_rebuild_reflects_current_chroma_state_after_delete(stores):
    vector_store, keyword_index = stores
    keep = _chunk_dict("evergreen tidal content", source_doc="keep.pdf")
    doomed = _chunk_dict("unique antidisestablishmentarianism marker", source_doc="doomed.pdf")
    index_chunks([keep, doomed], vector_store, keyword_index)
    assert len(keyword_index) == 2

    vector_store.delete_by_document("doomed.pdf", "persistent")
    # Re-index with nothing new: still a mutation happened elsewhere, so a
    # caller would rebuild; index_chunks([]) is a noop, so rebuild explicitly.
    keyword_index.rebuild()

    assert len(keyword_index) == 1
    assert keyword_index.search("antidisestablishmentarianism", k=5) == []


def test_get_stores_are_lazy_process_singletons(monkeypatch):
    monkeypatch.setattr(indexer, "_vector_store", None)
    monkeypatch.setattr(indexer, "_keyword_index", None)

    assert indexer.get_vector_store() is indexer.get_vector_store()
    assert indexer.get_keyword_index() is indexer.get_keyword_index()


def test_index_chunks_defaults_to_the_singletons():
    chunks = [_chunk_dict("chunk written to the default stores")]

    result = index_chunks(chunks)  # no explicit stores

    assert result.total_indexed == 1
    assert indexer.get_vector_store().count() == 1
    assert len(indexer.get_keyword_index()) == 1
