import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
}


def error_body(code: str, message: str, fields: list[dict[str, str]] | None = None) -> dict:
    body: dict = {"code": code, "message": message}
    if fields is not None:
        body["fields"] = fields
    return {"error": body}


def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "bad_request")
    return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers)


def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {"field": ".".join(str(part) for part in e["loc"] if part != "body") or "body", "message": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(error_body("validation_error", "Request validation failed", fields), status_code=422)


def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error in request", exc_info=exc)
    return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
