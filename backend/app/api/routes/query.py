from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.request_log import log_query_outcome
from app.api.schemas import QUERY_ERRORS, QueryRequest, QueryResponse, build_query_response
from app.api.streaming import STREAM_HEADERS, stream_query
from app.chains import query_chain
from app.query.emitter import NullEmitter
from app.shared.session_store import InvalidSessionId, new_session_id, validate_issued_session_id

router = APIRouter()


def _session_id(request: QueryRequest) -> str:
    if request.session_id is None:
        return new_session_id()
    try:
        return validate_issued_session_id(request.session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query", response_model=QueryResponse, responses=QUERY_ERRORS)
async def query(request: QueryRequest, http: Request) -> QueryResponse:
    """Answer a question and return the whole result in one response."""
    session_id = _session_id(request)
    emitter = NullEmitter()
    result = await query_chain.ainvoke(
        {"question": request.question, "session_id": session_id, "emitter": emitter}
    )
    response = build_query_response(result, session_id, emitter)
    log_query_outcome(response, getattr(http.state, "request_id", None))
    return response


@router.post(
    "/query/stream",
    responses={
        200: {
            "description": (
                "Server-Sent Events: `stage` and `usage` events while the pipeline runs, then one terminal "
                "`result` (the QueryResponse body) or `error` event. See docs/observability.md."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **{code: spec for code, spec in QUERY_ERRORS.items() if code != 500},
    },
)
async def query_stream(request: QueryRequest, http: Request) -> StreamingResponse:
    """Answer a question, streaming pipeline progress. The answer itself arrives only in the final event."""
    session_id = _session_id(request)
    return StreamingResponse(
        stream_query(request.question, session_id, getattr(http.state, "request_id", None)),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )
