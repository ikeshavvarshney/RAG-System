import uuid

import pytest

from app.core.config import settings
from app.shared import keyword_index
from app.shared.keyword_index import KeywordIndex, tokenize
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import VectorStore


def _chunk(
    text: str,
    *,
    source_doc: str = "doc.pdf",
    corpus_scope: str = "persistent",
) -> Chunk:
    return Chunk(
        chunk_id=str(uuid.uuid4()),
        text=text,
        source_doc=source_doc,
        page=1,
        location=None,
        chunk_type="text",
        extraction_method="text",
        corpus_scope=corpus_scope,
    )


def _vec(seed: float, dim: int = 8) -> list[float]:
    return [seed + i * 0.01 for i in range(dim)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHROMA_PATH", str(tmp_path / "chroma"))
    return VectorStore()


def _populate(store: VectorStore, chunks: list[Chunk]) -> None:
    store.upsert([(c, _vec(0.1 + i * 0.01)) for i, c in enumerate(chunks)])


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #
def test_tokenize_lowercases_strips_punctuation_splits_on_whitespace():
    assert tokenize("Hello, WORLD!  Foo-bar.\tBaz\nQux") == [
        "hello",
        "world",
        "foobar",
        "baz",
        "qux",
    ]
    assert tokenize("   ") == []
    assert tokenize("!!!") == []


# --------------------------------------------------------------------------- #
# Build / rebuild
# --------------------------------------------------------------------------- #
def test_distinctive_term_retrieves_the_right_chunk(store):
    target = _chunk("the aardvark shuffled across the veranda at dusk")
    others = [
        _chunk("quarterly revenue rose on strong cloud demand"),
        _chunk("the committee approved the zoning variance"),
        _chunk("rainfall totals exceeded seasonal averages"),
    ]
    _populate(store, [target, *others])

    index = KeywordIndex(store)
    index.rebuild()

    results = index.search("aardvark", k=3)

    assert results, "expected at least one hit for a distinctive term"
    assert results[0][0] == target.chunk_id
    assert results[0][1] > 0.0


def test_cold_start_rebuild_produces_a_working_index(store):
    chunks = [
        _chunk("alpha bravo charlie"),
        _chunk("delta echo foxtrot"),
        _chunk("golf hotel india"),
    ]
    _populate(store, chunks)

    # Classmethod path: construct + populate in one call.
    index = KeywordIndex.rebuild_from(store)

    hits = index.search("foxtrot", k=5)
    assert [cid for cid, _ in hits] == [chunks[1].chunk_id]


def test_rebuild_is_idempotent_and_repeatable(store):
    chunks = [
        _chunk("repeatable tokens here"),
        _chunk("other unrelated content"),
        _chunk("a third document about something else entirely"),
    ]
    _populate(store, chunks)

    index = KeywordIndex(store)
    index.rebuild()
    first = index.search("repeatable", k=5)
    index.rebuild()
    second = index.search("repeatable", k=5)

    assert first == second
    assert first[0][0] == chunks[0].chunk_id


def test_search_on_empty_store_returns_empty(store):
    index = KeywordIndex(store)
    index.rebuild()
    assert index.search("anything", k=5) == []


def test_results_are_ranked_descending_by_score(store):
    chunks = [
        _chunk("signal signal signal signal padding"),
        _chunk("signal padding padding padding padding"),
        _chunk("unrelated filler about weather patterns"),
        _chunk("another unrelated document concerning finance"),
        _chunk("yet more filler text without the keyword"),
    ]
    _populate(store, chunks)
    index = KeywordIndex.rebuild_from(store)

    hits = index.search("signal", k=5)
    assert {cid for cid, _ in hits} == {chunks[0].chunk_id, chunks[1].chunk_id}
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0][0] == chunks[0].chunk_id  # most occurrences ranks first


# --------------------------------------------------------------------------- #
# Shared tokenizer (index build path and query path must not diverge)
# --------------------------------------------------------------------------- #
def test_build_and_query_paths_call_the_same_tokenizer(store, monkeypatch):
    _populate(store, [_chunk("shared tokenizer probe text")])

    real = keyword_index.tokenize
    calls: list[str] = []

    def spy(text: str) -> list[str]:
        calls.append(text)
        return real(text)

    monkeypatch.setattr(keyword_index, "tokenize", spy)

    index = KeywordIndex(store)
    index.rebuild()
    after_build = len(calls)
    assert after_build > 0, "rebuild() did not go through keyword_index.tokenize"

    index.search("probe", k=3)
    assert len(calls) > after_build, "search() did not go through keyword_index.tokenize"


# --------------------------------------------------------------------------- #
# corpus_scope filtering
# --------------------------------------------------------------------------- #
def test_corpus_scope_filter_excludes_other_scope(store):
    persistent_chunk = _chunk(
        "the persistent knowledge base mentions photosynthesis",
        source_doc="kb.pdf",
        corpus_scope="persistent",
    )
    session_chunk = _chunk(
        "this session upload discusses flibbertigibbet at length",
        source_doc="upload.pdf",
        corpus_scope="session",
    )
    filler = _chunk(
        "an unrelated persistent note about migratory birds",
        source_doc="birds.pdf",
        corpus_scope="persistent",
    )
    _populate(store, [persistent_chunk, session_chunk, filler])
    index = KeywordIndex.rebuild_from(store)

    # Term lives only in the session-scope chunk.
    unscoped = index.search("flibbertigibbet", k=5)
    assert [cid for cid, _ in unscoped] == [session_chunk.chunk_id]

    scoped = index.search("flibbertigibbet", k=5, corpus_scope="persistent")
    assert scoped == []

    scoped_session = index.search("flibbertigibbet", k=5, corpus_scope="session")
    assert [cid for cid, _ in scoped_session] == [session_chunk.chunk_id]


# --------------------------------------------------------------------------- #
# D-22: delete-then-rebuild consistency
# --------------------------------------------------------------------------- #
def test_delete_document_then_rebuild_drops_its_terms(store):
    keep = _chunk("evergreen content about tidal patterns", source_doc="keep.pdf")
    doomed_a = _chunk(
        "unique marker antidisestablishmentarianism appears here",
        source_doc="doomed.pdf",
    )
    doomed_b = _chunk("more doomed content, also removable", source_doc="doomed.pdf")
    _populate(store, [keep, doomed_a, doomed_b])

    index = KeywordIndex(store)
    index.rebuild()
    assert [cid for cid, _ in index.search("antidisestablishmentarianism", k=5)] == [
        doomed_a.chunk_id
    ]

    deleted = store.delete_by_document("doomed.pdf", "persistent")
    assert set(deleted) == {doomed_a.chunk_id, doomed_b.chunk_id}

    index.rebuild()
    assert index.search("antidisestablishmentarianism", k=5) == []
    # The surviving document is still searchable.
    assert [cid for cid, _ in index.search("tidal", k=5)] == [keep.chunk_id]
