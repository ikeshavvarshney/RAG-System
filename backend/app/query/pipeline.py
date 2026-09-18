import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.core.config import settings
from app.ingestion.indexer import get_keyword_index, get_vector_store
from app.query.expansion import expand_query
from app.query.greeting import detect_greeting
from app.query.guardrails.input import check_input
from app.query.retrieval import RetrievalResult, retrieve
from app.shared.keyword_index import KeywordIndex
from app.shared.session_store import PERSISTENT_SCOPE
from app.shared.vector_store import VectorStore

Stage = Literal["guardrail", "greeting", "expansion", "retrieval"]


@dataclass(frozen=True)
class StageEvent:
    stage: Stage
    status: Literal["started", "completed"]


@dataclass
class QueryResult:
    terminated_at: Literal["guardrail", "greeting", "retrieved"]
    raw_question: str
    resolved_question: str
    retrieval: RetrievalResult = field(default_factory=RetrievalResult)
    expanded_queries: list[str] = field(default_factory=list)
    response: str | None = None


EventCallback = Callable[[StageEvent], None]


def _stores() -> tuple[VectorStore, KeywordIndex]:
    return get_vector_store(), get_keyword_index()


async def run_query(question: str, on_event: EventCallback | None = None) -> QueryResult:
    def emit(stage: Stage, status: Literal["started", "completed"]) -> None:
        if on_event:
            on_event(StageEvent(stage, status))

    emit("guardrail", "started")
    verdict = await check_input(question)
    emit("guardrail", "completed")
    if not verdict.allowed:
        return QueryResult("guardrail", question, verdict.sanitized, response=verdict.reason)

    emit("greeting", "started")
    reply = await detect_greeting(verdict.sanitized)
    emit("greeting", "completed")
    if reply is not None:
        return QueryResult("greeting", question, verdict.sanitized, response=reply)

    # [history slot]
    resolved_question = verdict.sanitized
    # [cache slot]

    emit("expansion", "started")
    queries = await expand_query(resolved_question)
    emit("expansion", "completed")

    emit("retrieval", "started")
    vector_store, keyword_index = await asyncio.to_thread(_stores)
    retrieval = await retrieve(
        resolved_question,
        queries,
        vector_store=vector_store,
        keyword_index=keyword_index,
        corpus_scope=PERSISTENT_SCOPE,
        top_k=settings.RETRIEVAL_TOP_K,
    )
    emit("retrieval", "completed")
    return QueryResult(
        "retrieved", question, resolved_question, retrieval=retrieval, expanded_queries=queries
    )
