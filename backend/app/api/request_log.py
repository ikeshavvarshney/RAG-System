import logging
import secrets
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.requests")

REQUEST_ID_HEADER = b"x-request-id"


class RequestLogMiddleware:
    """Log one line per HTTP request once its body has been sent, so a streamed response is timed to its end."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", [])).get(REQUEST_ID_HEADER, b"").decode("latin-1")
        request_id = incoming[:64] or secrets.token_hex(6)
        scope.setdefault("state", {})["request_id"] = request_id
        began = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((REQUEST_ID_HEADER, request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            status = 500
            raise
        finally:
            elapsed_ms = (time.perf_counter() - began) * 1000
            path = scope.get("path", "")
            if path.endswith("/health") and status < 400:
                level = logging.DEBUG
            elif status >= 500:
                level = logging.ERROR
            elif status >= 400:
                level = logging.WARNING
            else:
                level = logging.INFO
            logger.log(level, "%s %s %d %.0fms id=%s", scope["method"], path, status, elapsed_ms, request_id)


def log_query_outcome(response, request_id: str | None) -> None:
    """One summary line per answered query: how it ended and what it cost."""
    corpus = sum(1 for c in response.citations if c.kind == "corpus")
    web = len(response.citations) - corpus
    score = response.groundedness.score if response.groundedness else None
    last_stage = response.stages[-1].stage if response.stages else "-"
    logger.info(
        "query id=%s session=%s last_stage=%s cache_hit=%s decomposed=%s citations=%d corpus/%d web "
        "groundedness=%s removed_claims=%d tokens=%d",
        request_id,
        response.session_id[:8],
        last_stage,
        response.cache_hit,
        response.decomposed,
        corpus,
        web,
        "-" if score is None else f"{score:.2f}",
        len(response.removed_claims),
        response.usage.total_tokens,
    )
    logger.debug("query id=%s question=%r resolved=%r", request_id, response.raw_question, response.resolved_question)
