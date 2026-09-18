import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from app.core.config import settings
from app.ingestion.indexer import get_keyword_index, get_vector_store
from app.query.expansion import expand_query
from app.query.greeting import classify_greeting, match_greeting
from app.query.guardrails.input import check_deterministic, check_llm
from app.query.retrieval import RetrievalResult, retrieve
from app.shared.keyword_index import KeywordIndex
from app.shared.session_store import PERSISTENT_SCOPE
from app.shared.vector_store import VectorStore

Stage = Literal["guardrail", "greeting", "guardrail_llm", "greeting_llm", "expansion", "retrieval"]


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
    @contextmanager
    def stage(name: Stage) -> Iterator[None]:
        if on_event:
            on_event(StageEvent(name, "started"))
        yield
        if on_event:
            on_event(StageEvent(name, "completed"))

    with stage("guardrail"):
        verdict = check_deterministic(question)
    if not verdict.allowed:
        return QueryResult("guardrail", question, verdict.sanitized, response=verdict.reason)
    sanitized = verdict.sanitized

    with stage("greeting"):
        reply = match_greeting(sanitized)
    if reply is not None:
        return QueryResult("greeting", question, sanitized, response=reply)

    with stage("guardrail_llm"):
        verdict = await check_llm(sanitized)
    if not verdict.allowed:
        return QueryResult("guardrail", question, sanitized, response=verdict.reason)

    with stage("greeting_llm"):
        reply = await classify_greeting(sanitized)
    if reply is not None:
        return QueryResult("greeting", question, sanitized, response=reply)

    # [history slot]
    resolved_question = sanitized
    # [cache slot]

    with stage("expansion"):
        queries = await expand_query(resolved_question)

    with stage("retrieval"):
        vector_store, keyword_index = await asyncio.to_thread(_stores)
        retrieval = await retrieve(
            resolved_question,
            queries,
            vector_store=vector_store,
            keyword_index=keyword_index,
            corpus_scope=PERSISTENT_SCOPE,
            top_k=settings.RETRIEVAL_TOP_K,
        )
    return QueryResult(
        "retrieved", question, resolved_question, retrieval=retrieval, expanded_queries=queries
    )
