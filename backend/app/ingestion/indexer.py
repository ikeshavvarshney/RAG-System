"""Merge + write stage of ingestion (INGEST-04 final stage).

Takes the chunks produced by the pipeline, embeds them, upserts the
``(chunk, vector)`` pairs into the persistent vector store, then rebuilds the
BM25 keyword index in full (D-22: no incremental BM25 update — always a full
rebuild from Chroma after a mutation).

Shared-resource placement
-------------------------
``VectorStore`` and ``KeywordIndex`` are process-wide singletons, created once
on first use via :func:`get_vector_store` / :func:`get_keyword_index` — the same
"one instance per process" shape the codebase already uses for the key rotator
(``app.core.key_rotation``) and ``GeminiClient`` (``embedder._client`` /
``vision._client``). They are built lazily rather than at import so tests can
point ``settings.CHROMA_PATH`` at a temp directory first, and so importing this
module never opens a Chroma client as a side effect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ingestion.embedder import embed_chunks
from app.shared.keyword_index import KeywordIndex
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import VectorStore

logger = logging.getLogger(__name__)

_EXTRACTION_METHODS = ("text", "ocr", "vision")

_vector_store: VectorStore | None = None
_keyword_index: KeywordIndex | None = None


def get_vector_store() -> VectorStore:
    """The process-wide :class:`VectorStore`, created on first use."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


def get_keyword_index() -> KeywordIndex:
    """The process-wide :class:`KeywordIndex`, built from the vector store on
    first use (D-22 full rebuild)."""
    global _keyword_index
    if _keyword_index is None:
        _keyword_index = KeywordIndex.rebuild_from(get_vector_store())
    return _keyword_index


@dataclass
class IndexResult:
    """What one :func:`index_chunks` call wrote, plus current store totals."""

    total_indexed: int = 0
    by_extraction_method: dict[str, int] = field(
        default_factory=lambda: {method: 0 for method in _EXTRACTION_METHODS}
    )
    vector_store_total: int = 0
    keyword_index_total: int = 0


def index_chunks(
    chunks: list[Chunk | dict],
    vector_store: VectorStore | None = None,
    keyword_index: KeywordIndex | None = None,
) -> IndexResult:
    """Embed ``chunks``, upsert them into the vector store, rebuild the keyword
    index, and report what was written.

    Idempotent: upsert is keyed by ``chunk_id`` (re-indexing the same chunks
    updates in place, never duplicates), and the BM25 rebuild always reflects
    the current Chroma state afterwards.

    ``vector_store`` / ``keyword_index`` default to the process singletons; pass
    explicit instances in tests.
    """
    store = vector_store if vector_store is not None else get_vector_store()
    kw_index = keyword_index if keyword_index is not None else get_keyword_index()

    models = [_as_chunk(item) for item in chunks]

    if models:
        pairs = embed_chunks(models)
        stored = store.upsert(pairs)
        # D-22: full rebuild after the mutation, never an incremental update.
        kw_index.rebuild()
    else:
        stored = []

    counts = {method: 0 for method in _EXTRACTION_METHODS}
    for chunk in stored:
        counts[chunk.extraction_method] = counts.get(chunk.extraction_method, 0) + 1

    result = IndexResult(
        total_indexed=len(stored),
        by_extraction_method=counts,
        vector_store_total=store.count(),
        keyword_index_total=len(kw_index),
    )
    logger.info(
        "indexed %d chunks (%s); store now holds %d",
        result.total_indexed,
        ", ".join(f"{m}={counts[m]}" for m in _EXTRACTION_METHODS),
        result.vector_store_total,
    )
    return result


def _as_chunk(item: Chunk | dict) -> Chunk:
    return item if isinstance(item, Chunk) else Chunk(**item)
