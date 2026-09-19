import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass

import chromadb
from pydantic import TypeAdapter

from app.core.config import settings
from app.ingestion.embedder import embed_queries
from app.shared.schemas.citation import Citation, CorpusCitation

_COLLECTION_NAME = "answer_cache"
_CITATIONS = TypeAdapter(list[Citation])
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CANDIDATES = 5


@dataclass(frozen=True)
class CacheHit:
    answer: str
    citations: list[Citation]
    raw_question: str
    resolved_question: str
    cited_docs: list[str]
    corpus_scope: str
    created_at: float
    score: float


def _entry_id(scope: str, resolved_question: str) -> str:
    return hashlib.sha256(f"{scope}\x00{resolved_question}".encode("utf-8")).hexdigest()


# Chroma metadata cannot hold lists or match substrings, so each cited document
# becomes its own boolean key that a `where` filter can match exactly.
def _doc_key(document_id: str) -> str:
    return "doc:" + hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:24]


# Embeddings barely register a changed year or figure: "FY 2023" vs "FY 2024" scored
# 0.987, above real paraphrases. A hit must therefore quote the same numbers.
def _numbers(text: str) -> Counter[str]:
    return Counter(match.replace(",", "") for match in _NUMBER.findall(text))


def _scope_filter(scope: str) -> dict:
    return {"corpus_scope": {"$eq": scope}}


class AnswerCache:
    def __init__(self, path: str | None = None):
        self._client = chromadb.PersistentClient(path=path or settings.CACHE_PATH)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        try:
            self._client.close()
            self._client.clear_system_cache()
        except Exception:  # noqa: BLE001 - closing must not raise on teardown
            pass

    def count(self) -> int:
        return self._collection.count()

    def lookup(
        self, resolved_question: str, scope: str, threshold: float | None = None
    ) -> CacheHit | None:
        if self._collection.count() == 0:
            return None
        limit = settings.CACHE_SIMILARITY_THRESHOLD if threshold is None else threshold
        vector = embed_queries([resolved_question])[0]
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=_CANDIDATES,
            where=_scope_filter(scope),
            include=["metadatas", "distances"],
        )
        wanted_numbers = _numbers(resolved_question)
        for distance, meta in zip(result["distances"][0], result["metadatas"][0]):
            score = 1.0 - float(distance)
            if score < limit:
                return None
            if _numbers(meta["resolved_question"]) != wanted_numbers:
                continue
            return CacheHit(
                answer=meta["answer"],
                citations=_CITATIONS.validate_json(meta["citations"]),
                raw_question=meta["raw_question"],
                resolved_question=meta["resolved_question"],
                cited_docs=json.loads(meta["cited_docs"]),
                corpus_scope=meta["corpus_scope"],
                created_at=meta["created_at"],
                score=score,
            )
        return None

    def put(
        self,
        raw_question: str,
        resolved_question: str,
        answer: str,
        citations: list[Citation],
        scope: str,
    ) -> None:
        cited_docs = sorted(
            {c.source_doc for c in citations if isinstance(c, CorpusCitation)}
        )
        metadata = {
            "raw_question": raw_question,
            "resolved_question": resolved_question,
            "answer": answer,
            "citations": _CITATIONS.dump_json(citations).decode(),
            "cited_docs": json.dumps(cited_docs),
            "corpus_scope": scope,
            "created_at": time.time(),
            **{_doc_key(doc): True for doc in cited_docs},
        }
        self._collection.upsert(
            ids=[_entry_id(scope, resolved_question)],
            embeddings=[embed_queries([resolved_question])[0]],
            documents=[resolved_question],
            metadatas=[metadata],
        )

    def invalidate_by_document(self, document_id: str, scope: str | None = None) -> int:
        where: dict = {_doc_key(document_id): {"$eq": True}}
        if scope is not None:
            where = {"$and": [_scope_filter(scope), where]}
        return self._delete(where)

    def invalidate_scope(self, scope: str) -> int:
        return self._delete(_scope_filter(scope))

    def _delete(self, where: dict) -> int:
        ids = self._collection.get(where=where, include=[])["ids"]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)


_cache: AnswerCache | None = None


def get_answer_cache() -> AnswerCache:
    global _cache
    if _cache is None:
        _cache = AnswerCache()
    return _cache


def lookup(resolved_question: str, scope: str) -> CacheHit | None:
    return get_answer_cache().lookup(resolved_question, scope)


def put(
    raw_question: str,
    resolved_question: str,
    answer: str,
    citations: list[Citation],
    scope: str,
) -> None:
    get_answer_cache().put(raw_question, resolved_question, answer, citations, scope)


def invalidate_by_document(document_id: str, scope: str | None = None) -> int:
    return get_answer_cache().invalidate_by_document(document_id, scope)


def invalidate_scope(scope: str) -> int:
    return get_answer_cache().invalidate_scope(scope)
