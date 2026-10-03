import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from app.core.config import settings
from app.ingestion.indexer import get_keyword_index, get_vector_store
from app.query import cache
from app.query.decomposition import decompose
from app.query.expansion import expand_query
from app.query.fusion import Candidate, fuse
from app.query.citations import (
    CitationFilterResult,
    GroundednessResult,
    RemovedClaim,
    apply_semantic_verdicts,
    filter_structural,
)
from app.query.generation import GenerationResult, generate_answer
from app.query.greeting import classify_greeting, match_greeting
from app.query.guardrails.input import check_deterministic, check_llm
from app.query.guardrails.output import SafetyVerdict, apply_output_guardrail
from app.query.history import (
    FAILED_SUMMARY,
    NON_ANSWER_SUMMARY,
    record_turn,
    resolve_question,
    summarize_answer,
)
from app.query.rerank import RankedContext, rerank_and_consolidate
from app.query.retrieval import RetrievalResult, retrieve
from app.query.sufficiency import SufficiencyResult, assess
from app.query.verification import UNAVAILABLE, verify_answer
from app.query.web_search import WebSearchResult, search_web
from app.shared.keyword_index import KeywordIndex
from app.shared.schemas.citation import Citation
from app.shared.session_store import PERSISTENT_SCOPE, find_session_stores, scope_for
from app.shared.vector_store import VectorStore

logger = logging.getLogger(__name__)

Stage = Literal[
    "guardrail",
    "greeting",
    "guardrail_llm",
    "greeting_llm",
    "history",
    "cache",
    "decomposition",
    "expansion",
    "retrieval",
    "fusion",
    "sufficiency",
    "web_search",
    "rerank",
    "generation",
    "citations",
    "output_guardrail",
    "verification",
]


@dataclass(frozen=True)
class StageEvent:
    stage: Stage
    status: Literal["started", "completed", "failed"]
    duration_ms: float | None = None
    sub_question: int | None = None


@dataclass
class SubQuery:
    question: str
    expanded_queries: list[str]
    retrieval: RetrievalResult
    fused: list[Candidate]
    sufficiency: SufficiencyResult
    web_search: WebSearchResult | None
    context: list[Candidate]
    reranked: bool


@dataclass
class QueryResult:
    terminated_at: Literal["guardrail", "greeting", "cache_hit", "retrieved"]
    raw_question: str
    resolved_question: str
    retrieval: RetrievalResult = field(default_factory=RetrievalResult)
    expanded_queries: list[str] = field(default_factory=list)
    fused: list[Candidate] = field(default_factory=list)
    sufficiency: SufficiencyResult | None = None
    web_search: WebSearchResult | None = None
    context: list[Candidate] = field(default_factory=list)
    reranked: bool = False
    sub_queries: list[SubQuery] = field(default_factory=list)
    generation: GenerationResult | None = None
    groundedness: GroundednessResult | None = None
    safety: SafetyVerdict | None = None
    removed_claims: list[RemovedClaim] = field(default_factory=list)
    response: str | None = None
    answer: str | None = None
    citations: list[Citation] = field(default_factory=list)


EventCallback = Callable[[StageEvent], None]
StageContext = Callable[[Stage], AbstractContextManager[None]]


@dataclass
class RankedRetrieval:
    expanded_queries: list[str]
    retrieval: RetrievalResult
    fused: list[Candidate]
    sufficiency: SufficiencyResult
    web_search: WebSearchResult | None
    context: RankedContext


def _elapsed_ms(began: float) -> float:
    return round((time.perf_counter() - began) * 1000, 1)


def _select_scope(session_id: str) -> tuple[str, VectorStore, KeywordIndex]:
    """Session uploads answer alone when present, otherwise the corpus does."""
    session = find_session_stores(session_id)
    if session is not None:
        return scope_for(session_id), *session
    return PERSISTENT_SCOPE, get_vector_store(), get_keyword_index()


async def _lookup_cache(resolved_question: str, scope: str) -> cache.CacheHit | None:
    try:
        return await asyncio.to_thread(cache.lookup, resolved_question, scope)
    except Exception:  # noqa: BLE001 - a broken cache must not block retrieval
        logger.warning("answer cache lookup failed; continuing to retrieval", exc_info=True)
        return None


def _rerank_pool(fused: list[Candidate], web: list[Candidate]) -> list[Candidate]:
    """Web results take reserved slots so a full corpus shortlist cannot crowd them out.

    They lead the pool: if reranking fails the order is kept, and the corpus was already judged insufficient.
    """
    web = web[: min(settings.WEB_MAX_IN_POOL, settings.RERANK_CANDIDATES)]
    return web + fused[: settings.RERANK_CANDIDATES - len(web)]


async def retrieve_and_rank(
    question: str,
    stage: StageContext,
    *,
    corpus_scope: str,
    vector_store: VectorStore,
    keyword_index: KeywordIndex,
) -> RankedRetrieval:
    with stage("expansion"):
        queries = await expand_query(question)

    with stage("retrieval"):
        retrieval = await retrieve(
            question,
            queries,
            vector_store=vector_store,
            keyword_index=keyword_index,
            corpus_scope=corpus_scope,
            top_k=settings.RETRIEVAL_TOP_K,
        )

    with stage("fusion"):
        fused = fuse(retrieval, dense_weight=settings.FUSION_DENSE_WEIGHT, k=settings.RRF_K)

    with stage("sufficiency"):
        sufficiency = await assess(question, fused)

    web_search: WebSearchResult | None = None
    if not sufficiency.sufficient:
        with stage("web_search"):
            web_search = await search_web(question)

    with stage("rerank"):
        pool = _rerank_pool(fused, web_search.candidates if web_search else [])
        context = await asyncio.to_thread(rerank_and_consolidate, question, pool)

    return RankedRetrieval(queries, retrieval, fused, sufficiency, web_search, context)


async def _retrieve_sub_questions(
    sub_questions: list[str],
    stage: Callable[..., AbstractContextManager[None]],
    *,
    corpus_scope: str,
    vector_store: VectorStore,
    keyword_index: KeywordIndex,
) -> list[SubQuery]:
    outcomes = await asyncio.gather(
        *(
            retrieve_and_rank(
                question,
                partial(stage, sub_question=index),
                corpus_scope=corpus_scope,
                vector_store=vector_store,
                keyword_index=keyword_index,
            )
            for index, question in enumerate(sub_questions)
        ),
        return_exceptions=True,
    )
    # Every sibling has finished by now, so raising cannot orphan a running retrieval.
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return [
        SubQuery(
            question,
            ranked.expanded_queries,
            ranked.retrieval,
            ranked.fused,
            ranked.sufficiency,
            ranked.web_search,
            ranked.context.passages,
            ranked.context.reranked,
        )
        for question, ranked in zip(sub_questions, outcomes)
    ]


@dataclass
class FinalAnswer:
    generation: GenerationResult
    answer: str
    citations: list[Citation]
    groundedness: GroundednessResult
    safety: SafetyVerdict
    removed_claims: list[RemovedClaim]
    summary: str
    cacheable: bool


def _passage_texts(candidates: list[Candidate]) -> dict[str, str]:
    """Full passage text keyed as verification expects: chunk_id for corpus, source_url for web."""
    texts: dict[str, str] = {}
    for candidate in candidates:
        key = candidate.metadata.get("source_url") if candidate.source == "web" else candidate.chunk_id
        if key:
            texts[key] = candidate.text
    return texts


async def finalize_answer(
    question: str,
    stage: StageContext,
    *,
    passages: list[Candidate] | None = None,
    sub_contexts: list[tuple[str, list[Candidate]]] | None = None,
    corpus_scope: str,
) -> FinalAnswer:
    """Generate, then check the answer structurally, semantically and for safety."""
    generation = await generate_answer(question, passages, sub_contexts=sub_contexts, stage=stage)
    filtered: CitationFilterResult = filter_structural(generation, stage=stage)
    candidates = list(passages or []) + [c for _, group in sub_contexts or [] for c in group]
    verification = await verify_answer(filtered, _passage_texts(candidates), stage=stage)
    semantic = apply_semantic_verdicts(filtered, verification.groundedness)
    guarded = apply_output_guardrail(semantic.answer, verification.safety, stage=stage)

    if not guarded.passed:
        logger.warning("answer replaced by the safe fallback: %s", guarded.verdict.reason)
        answer, citations, summary = guarded.answer, [], FAILED_SUMMARY
    elif semantic.is_non_answer:
        answer, citations, summary = guarded.answer, [], NON_ANSWER_SUMMARY
    else:
        answer, citations, summary = guarded.answer, semantic.citations, summarize_answer(guarded.answer)

    # Only a verified, safe, real answer to a corpus question is worth serving again.
    cacheable = (
        guarded.passed
        and not semantic.is_non_answer
        and bool(citations)
        and verification.safety.reason != UNAVAILABLE
        and corpus_scope == PERSISTENT_SCOPE
    )
    return FinalAnswer(
        generation,
        answer,
        citations,
        semantic.groundedness or verification.groundedness,
        guarded.verdict,
        semantic.removed_claims,
        summary,
        cacheable,
    )


async def _write_cache(raw_question: str, resolved_question: str, final: FinalAnswer, scope: str) -> None:
    if not final.cacheable:
        return
    try:
        await asyncio.to_thread(
            cache.put, raw_question, resolved_question, final.answer, final.citations, scope
        )
    except Exception:  # noqa: BLE001 - a broken cache must not fail an answered query
        logger.warning("answer cache write failed; the answer is still returned", exc_info=True)


async def run_query(
    question: str, session_id: str, on_event: EventCallback | None = None
) -> QueryResult:
    @contextmanager
    def stage(name: Stage, sub_question: int | None = None) -> Iterator[None]:
        if on_event:
            on_event(StageEvent(name, "started", sub_question=sub_question))
        began = time.perf_counter()
        try:
            yield
        except BaseException:
            if on_event:
                on_event(StageEvent(name, "failed", _elapsed_ms(began), sub_question))
            raise
        if on_event:
            on_event(StageEvent(name, "completed", _elapsed_ms(began), sub_question))

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

    with stage("history"):
        _, resolved_question = await resolve_question(session_id, sanitized)

    corpus_scope, vector_store, keyword_index = await asyncio.to_thread(
        _select_scope, session_id
    )

    with stage("cache"):
        hit = await _lookup_cache(resolved_question, corpus_scope)
    if hit is not None:
        record_turn(session_id, sanitized, resolved_question, summarize_answer(hit.answer))
        return QueryResult(
            "cache_hit",
            question,
            resolved_question,
            answer=hit.answer,
            citations=hit.citations,
        )

    with stage("decomposition"):
        sub_questions = await decompose(resolved_question)
    if sub_questions:
        sub_queries = await _retrieve_sub_questions(
            sub_questions,
            stage,
            corpus_scope=corpus_scope,
            vector_store=vector_store,
            keyword_index=keyword_index,
        )
        final = await finalize_answer(
            resolved_question,
            stage,
            sub_contexts=[(sub.question, sub.context) for sub in sub_queries],
            corpus_scope=corpus_scope,
        )
        await _write_cache(sanitized, resolved_question, final, corpus_scope)
        record_turn(session_id, sanitized, resolved_question, final.summary)
        return QueryResult(
            "retrieved",
            question,
            resolved_question,
            sub_queries=sub_queries,
            generation=final.generation,
            answer=final.answer,
            citations=final.citations,
            groundedness=final.groundedness,
            safety=final.safety,
            removed_claims=final.removed_claims,
        )

    ranked = await retrieve_and_rank(
        resolved_question,
        stage,
        corpus_scope=corpus_scope,
        vector_store=vector_store,
        keyword_index=keyword_index,
    )
    final = await finalize_answer(
        resolved_question, stage, passages=ranked.context.passages, corpus_scope=corpus_scope
    )
    await _write_cache(sanitized, resolved_question, final, corpus_scope)
    record_turn(session_id, sanitized, resolved_question, final.summary)
    return QueryResult(
        "retrieved",
        question,
        resolved_question,
        generation=final.generation,
        answer=final.answer,
        citations=final.citations,
        groundedness=final.groundedness,
        safety=final.safety,
        removed_claims=final.removed_claims,
        retrieval=ranked.retrieval,
        expanded_queries=ranked.expanded_queries,
        fused=ranked.fused,
        sufficiency=ranked.sufficiency,
        web_search=ranked.web_search,
        context=ranked.context.passages,
        reranked=ranked.context.reranked,
    )
