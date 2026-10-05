import logging

from fastapi.testclient import TestClient

from app.main import create_app


def _request_lines(caplog):
    return [r for r in caplog.records if r.name == "app.requests"]


def test_request_is_logged_with_status_and_request_id(caplog):
    client = TestClient(create_app())
    with caplog.at_level(logging.INFO, logger="app"):
        r = client.get("/api/nothing-here")
    lines = _request_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert f"GET /api/nothing-here {r.status_code}" in lines[0].getMessage()
    assert f"id={r.headers['x-request-id']}" in lines[0].getMessage()


def test_incoming_request_id_is_kept():
    client = TestClient(create_app())
    r = client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_health_checks_log_at_debug_only(caplog):
    client = TestClient(create_app())
    with caplog.at_level(logging.INFO, logger="app"):
        client.get("/api/health")
    assert _request_lines(caplog) == []
