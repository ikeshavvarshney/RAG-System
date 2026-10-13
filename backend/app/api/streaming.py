import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio

from app.api.request_log import log_query_outcome
from app.api.schemas import build_query_response
from app.chains import query_chain
from app.query.emitter import DONE, QueueEmitter

logger = logging.getLogger(__name__)

STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
STREAM_ERROR_MESSAGE = "The query could not be completed."


def format_sse(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _run_pipeline(question: str, session_id: str, emitter: QueueEmitter, request_id: str | None) -> None:
    try:
        result = await query_chain.ainvoke({"question": question, "session_id": session_id, "emitter": emitter})
        response = build_query_response(result, session_id, emitter)
        log_query_outcome(response, request_id)
        emitter.finish("result", response.model_dump(mode="json"))
    except asyncio.CancelledError:
        logger.info("query id=%s cancelled: client disconnected", request_id)
        raise
    except Exception:  # noqa: BLE001 - the client gets a generic error event, the log gets the trace
        logger.exception("query id=%s pipeline failed", request_id)
        emitter.finish("error", {"code": "internal_error", "message": STREAM_ERROR_MESSAGE})
    finally:
        emitter.close()


async def stream_query(question: str, session_id: str, request_id: str | None = None) -> AsyncIterator[str]:
    """Yield SSE frames for one query. Closing the generator (client disconnect) cancels the pipeline."""
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    emitter = QueueEmitter(queue)
    task = asyncio.create_task(_run_pipeline(question, session_id, emitter, request_id))
    try:
        while True:
            name, payload = await queue.get()
            if (name, payload) == DONE:
                return
            yield format_sse(name, payload)
    finally:
        if not task.done():
            task.cancel()
        # Shielded so the wait itself survives the cancellation that closed the response.
        with anyio.CancelScope(shield=True):
            await asyncio.gather(task, return_exceptions=True)
