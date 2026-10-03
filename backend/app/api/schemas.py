from typing import Any, Literal

from pydantic import BaseModel, Field

from app.query.citations import GroundednessResult, RemovedClaim
from app.query.emitter import NullEmitter
from app.query.guardrails.output import SafetyVerdict
from app.query.pipeline import QueryResult
from app.shared.schemas.citation import Citation


class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None


class StageTiming(BaseModel):
    stage: str
    status: Literal["completed", "failed"]
    duration_ms: float | None = None
    sub_question: int | None = None


class StageUsage(BaseModel):
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class UsageSummary(BaseModel):
    total_tokens: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    by_stage: dict[str, StageUsage] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    stages: list[StageTiming] = Field(default_factory=list)
    cache_hit: bool
    decomposed: bool
    sub_questions: list[str] | None = None
    raw_question: str
    resolved_question: str
    usage: UsageSummary
    groundedness: GroundednessResult | None = None
    safety: SafetyVerdict | None = None
    removed_claims: list[RemovedClaim] = Field(default_factory=list)


class ErrorField(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    fields: list[ErrorField] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def summarize_usage(emitter: NullEmitter) -> UsageSummary:
    summary = UsageSummary()
    for call in emitter.usage_events:
        stage = summary.by_stage.setdefault(call["stage"], StageUsage())
        for target in (stage, summary):
            target.prompt_tokens += call["prompt_tokens"]
            target.output_tokens += call["output_tokens"]
            target.total_tokens += call["total_tokens"]
    return summary


def build_query_response(result: QueryResult, session_id: str, emitter: NullEmitter) -> QueryResponse:
    """Serialize a pipeline result. Early exits (greeting, rejected input) answer with their `response`."""
    sub_questions = [sub.question for sub in result.sub_queries] or None
    return QueryResponse(
        session_id=session_id,
        answer=result.answer if result.answer is not None else (result.response or ""),
        citations=result.citations,
        stages=[
            StageTiming(stage=e.stage, status=e.status, duration_ms=e.duration_ms, sub_question=e.sub_question)
            for e in emitter.stage_events
            if e.status != "started"
        ],
        cache_hit=result.terminated_at == "cache_hit",
        decomposed=sub_questions is not None,
        sub_questions=sub_questions,
        raw_question=result.raw_question,
        resolved_question=result.resolved_question,
        usage=summarize_usage(emitter),
        groundedness=result.groundedness,
        safety=result.safety,
        removed_claims=result.removed_claims,
    )


QUERY_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Malformed session id"},
    422: {"model": ErrorResponse, "description": "Request body failed validation"},
    500: {"model": ErrorResponse, "description": "Unexpected failure"},
}


ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Malformed session id or request"},
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "Session document limit reached"},
    413: {"model": ErrorResponse, "description": "Too many files"},
    422: {"model": ErrorResponse, "description": "Request failed validation"},
    500: {"model": ErrorResponse, "description": "Unexpected failure"},
}


class SessionResponse(BaseModel):
    session_id: str


class IndexSummary(BaseModel):
    total: int
    by_extraction_method: dict[str, int]
    failed: int
    failure_reason: str | None = None
    vector_store_total: int
    keyword_index_total: int


class FileFailure(BaseModel):
    filename: str | None = None
    reason: str


class IngestResponse(BaseModel):
    chunk_count: int
    indexed: IndexSummary
    succeeded: list[str]
    failed: list[FileFailure]
    corpus_scope: str


class DocumentInfo(BaseModel):
    source_doc: str
    chunk_count: int
    pages: int | None = None
    extraction_methods: list[str]


class DocumentList(BaseModel):
    documents: list[DocumentInfo]


class DeleteDocumentResponse(BaseModel):
    source_doc: str
    deleted_chunks: int
    invalidated_cache_entries: int
