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
from itertools import batched

from app.ingestion.embedder import EMBED_BATCH_SIZE, embed_chunks
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
    """What one :func:`index_chunks` call wrote, plus current store totals.

    ``failed_chunks`` / ``failure_reason`` are set when a batch failed partway
    through: everything up to that batch is already persisted, the rest of this
    run's chunks were not embedded, and the caller can report / resume.
    """

    total_indexed: int = 0
    by_extraction_method: dict[str, int] = field(
        default_factory=lambda: {method: 0 for method in _EXTRACTION_METHODS}
    )
    failed_chunks: int = 0
    failure_reason: str | None = None
    vector_store_total: int = 0
    keyword_index_total: int = 0


def index_chunks(
    chunks: list[Chunk | dict],
    vector_store: VectorStore | None = None,
    keyword_index: KeywordIndex | None = None,
) -> IndexResult:
    """Embed ``chunks`` batch by batch, upserting each batch into the vector
    store as soon as it succeeds, then rebuild the keyword index once.

    Partial-failure tolerant: if a batch's embed or upsert raises, every batch
    already persisted stays persisted — only this run's unembedded remainder is
    lost. The returned :class:`IndexResult` reports ``total_indexed`` vs
    ``failed_chunks`` (+ ``failure_reason``) so the caller reports it honestly;
    ``index_chunks`` does not re-raise.

    Idempotent: upsert is keyed by ``chunk_id``. The BM25 rebuild (D-22: full,
    never incremental) runs once at the end, over whatever is in Chroma by then.

    ``vector_store`` / ``keyword_index`` default to the process singletons; pass
    explicit instances in tests.
    """
    store = vector_store if vector_store is not None else get_vector_store()
    kw_index = keyword_index if keyword_index is not None else get_keyword_index()

    models = [_as_chunk(item) for item in chunks]

    indexed: list[Chunk] = []
    failure_reason: str | None = None
    mutated = False

    for batch in batched(models, EMBED_BATCH_SIZE):
        try:
            pairs = embed_chunks(list(batch))
            stored = store.upsert(pairs)  # persist this batch immediately
        except Exception as exc:  # noqa: BLE001 - report, don't abort (cf. D-19)
            failure_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "index_chunks: batch failed after %d/%d chunks persisted; "
                "keeping earlier batches. %s",
                len(indexed),
                len(models),
                failure_reason,
            )
            break
        indexed.extend(stored)
        mutated = True

    if mutated:
        # D-22: one full rebuild from whatever is now in Chroma, not per batch.
        kw_index.rebuild()

    counts = {method: 0 for method in _EXTRACTION_METHODS}
    for chunk in indexed:
        counts[chunk.extraction_method] = counts.get(chunk.extraction_method, 0) + 1

    result = IndexResult(
        total_indexed=len(indexed),
        by_extraction_method=counts,
        failed_chunks=len(models) - len(indexed),
        failure_reason=failure_reason,
        vector_store_total=store.count(),
        keyword_index_total=len(kw_index),
    )
    logger.info(
        "indexed %d/%d chunks (%s); store holds %d%s",
        result.total_indexed,
        len(models),
        ", ".join(f"{m}={counts[m]}" for m in _EXTRACTION_METHODS),
        result.vector_store_total,
        f"; {result.failed_chunks} lost to {failure_reason}" if failure_reason else "",
    )
    return result


def _as_chunk(item: Chunk | dict) -> Chunk:
    return item if isinstance(item, Chunk) else Chunk(**item)
