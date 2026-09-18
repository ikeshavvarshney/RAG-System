import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from app.ingestion.embedder import embed_queries
from app.shared.keyword_index import KeywordIndex
from app.shared.schemas.chunk import Chunk
from app.shared.vector_store import VectorStore

_NON_METADATA_FIELDS = {"chunk_id", "text"}


@dataclass
class RetrievalHit:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    score: float
    retriever: Literal["vector", "keyword"]
    query: str | None = None


@dataclass
class RetrievalResult:
    vector_hits: list[RetrievalHit] = field(default_factory=list)
    keyword_hits: list[RetrievalHit] = field(default_factory=list)


def _hit(
    chunk: Chunk,
    score: float,
    retriever: Literal["vector", "keyword"],
    query: str | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        metadata=chunk.model_dump(exclude=_NON_METADATA_FIELDS),
        score=score,
        retriever=retriever,
        query=query,
    )


def _search_vectors(
    store: VectorStore, queries: list[str], vectors: list[list[float]], k: int, scope: str
) -> list[RetrievalHit]:
    hits: list[RetrievalHit] = []
    for query, vector in zip(queries, vectors):
        for result in store.search(vector, k, where={"corpus_scope": scope}):
            # Chroma reports cosine distance; flip it so higher is better, like BM25.
            hits.append(_hit(result.chunk, 1.0 - result.distance, "vector", query))
    return hits


async def _vector_retrieve(
    store: VectorStore, queries: list[str], k: int, scope: str
) -> list[RetrievalHit]:
    vectors = await asyncio.to_thread(embed_queries, queries)
    return await asyncio.to_thread(_search_vectors, store, queries, vectors, k, scope)


def _search_keywords(
    index: KeywordIndex, store: VectorStore, query: str, k: int, scope: str
) -> list[RetrievalHit]:
    scored = index.search(query, k, corpus_scope=scope)
    scores = dict(scored)
    chunks = store.get(list(scores))
    hits = [_hit(chunk, scores[chunk.chunk_id], "keyword") for chunk in chunks]
    return sorted(hits, key=lambda hit: hit.score, reverse=True)


async def _keyword_retrieve(
    index: KeywordIndex, store: VectorStore, query: str, k: int, scope: str
) -> list[RetrievalHit]:
    return await asyncio.to_thread(_search_keywords, index, store, query, k, scope)


async def retrieve(
    resolved_question: str,
    expanded_queries: list[str],
    *,
    vector_store: VectorStore,
    keyword_index: KeywordIndex,
    corpus_scope: str,
    top_k: int,
) -> RetrievalResult:
    vector_hits, keyword_hits = await asyncio.gather(
        _vector_retrieve(vector_store, expanded_queries, top_k, corpus_scope),
        _keyword_retrieve(keyword_index, vector_store, resolved_question, top_k, corpus_scope),
    )
    return RetrievalResult(vector_hits=vector_hits, keyword_hits=keyword_hits)
