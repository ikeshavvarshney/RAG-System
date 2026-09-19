import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.shared import session_store
from app.shared.session_store import new_session_id


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_STORE_ROOT", str(tmp_path / "sessions"))
    session_store.reset_cache()
    yield
    session_store.reset_cache()


def _upload(client, sid, names):
    files = [("files", (n, b"%PDF-1.4", "application/pdf")) for n in names]
    return client.post("/api/ingest", files=files, data={"session_id": sid})


def test_session_upload_over_document_limit_is_rejected_with_409(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_MAX_DOCUMENTS", 2)
    client = TestClient(create_app())

    response = _upload(client, new_session_id(), ["a.pdf", "b.pdf", "c.pdf"])

    assert response.status_code == 409
    assert "2" in response.json()["detail"]


def test_session_limit_counts_documents_already_held(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_MAX_DOCUMENTS", 2)
    sid = new_session_id()
    vector_store, _ = session_store.get_session_stores(sid)
    monkeypatch.setattr(
        type(vector_store),
        "documents",
        lambda self: [type("D", (), {"source_doc": n})() for n in ("a.pdf", "b.pdf")],
    )

    response = _upload(TestClient(create_app()), sid, ["c.pdf"])

    assert response.status_code == 409


def test_reuploading_a_held_document_does_not_count_against_the_limit(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_MAX_DOCUMENTS", 1)
    sid = new_session_id()
    vector_store, _ = session_store.get_session_stores(sid)
    monkeypatch.setattr(
        type(vector_store),
        "documents",
        lambda self: [type("D", (), {"source_doc": "a.pdf"})()],
    )

    response = _upload(TestClient(create_app()), sid, ["a.pdf"])

    assert response.status_code == 200
