from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from app.query.retrieval import RetrievalHit, RetrievalResult


@dataclass(frozen=True)
class Contribution:
    retriever: Literal["vector", "keyword"]
    rank: int
    query: str | None = None


@dataclass
class Candidate:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    score: float
    source: Literal["corpus", "web"] = "corpus"
    provenance: list[Contribution] = field(default_factory=list)


def _ranked(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    best: dict[str, RetrievalHit] = {}
    for hit in hits:
        if hit.chunk_id not in best or hit.score > best[hit.chunk_id].score:
            best[hit.chunk_id] = hit
    return sorted(best.values(), key=lambda hit: hit.score, reverse=True)


def fuse(retrieval: RetrievalResult, *, dense_weight: float, k: int) -> list[Candidate]:
    """Weighted RRF with the dense lists averaged into one channel.

    Each expanded query yields its own dense list while sparse search yields one, so
    the dense weight is split across its lists. Otherwise four dense votes against one
    sparse vote would skew the dense/sparse balance that RQ1 sweeps.
    """
    dense_lists: dict[str | None, list[RetrievalHit]] = defaultdict(list)
    for hit in retrieval.vector_hits:
        dense_lists[hit.query].append(hit)

    weighted = [(dense_weight / len(dense_lists), _ranked(hits)) for hits in dense_lists.values()]
    weighted.append((1.0 - dense_weight, _ranked(retrieval.keyword_hits)))

    fused: dict[str, Candidate] = {}
    for weight, hits in weighted:
        for rank, hit in enumerate(hits, start=1):
            candidate = fused.get(hit.chunk_id)
            if candidate is None:
                candidate = fused[hit.chunk_id] = Candidate(hit.chunk_id, hit.text, hit.metadata, 0.0)
            candidate.score += weight / (k + rank)
            candidate.provenance.append(Contribution(hit.retriever, rank, hit.query))

    # A zero-weight channel still records provenance but must not surface chunks on its own.
    ranked = [c for c in fused.values() if c.score > 0.0]
    return sorted(ranked, key=lambda c: c.score, reverse=True)
