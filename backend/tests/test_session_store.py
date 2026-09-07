import io
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.shared import session_store
from app.shared.session_store import (
    InvalidSessionId,
    drop_session,
    get_session_stores,
    new_session_id,
    purge_expired,
    scope_for,
    session_path,
    validate_issued_session_id,
    validate_session_id,
)
from app.shared.vector_store import VectorStore


@pytest.fixture(autouse=True)
def isolate_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_STORE_ROOT", str(tmp_path / "sessions"))
    session_store.reset_cache()
    yield
    session_store.reset_cache()


def _pdf_bytes(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


# --------------------------------------------------------------------------- #
# Session id validation. A session id becomes a directory name.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "session_id",
    ["../../chroma", "..", "a/b", "a\\b", "", "  ", "a" * 65, "-leading", "with space"],
)
def test_unsafe_session_ids_are_rejected(session_id):
    """A crafted id must not be able to name a directory outside the root.

    Without this, "../../chroma" would let an upload write into the corpus
    store, or a delete remove it.
    """
    with pytest.raises(InvalidSessionId):
        validate_session_id(session_id)


@pytest.mark.parametrize("session_id", ["abc123", "a", "A-B_c", "0" * 64])
def test_safe_session_ids_are_accepted(session_id):
    assert validate_session_id(session_id) == session_id


def test_session_path_stays_inside_the_root():
    root = session_store.session_root().resolve()
    assert str(session_path("abc123").resolve()).startswith(str(root))


def test_traversal_id_cannot_reach_the_corpus_store():
    with pytest.raises(InvalidSessionId):
        session_path("../chroma")


def test_scope_is_namespaced_by_session():
    assert scope_for("abc123") == "session:abc123"


# --------------------------------------------------------------------------- #
# Store isolation
# --------------------------------------------------------------------------- #

def test_each_session_gets_its_own_store():
    store_a, _ = get_session_stores("aaa")
    store_b, _ = get_session_stores("bbb")

    assert store_a is not store_b
    assert session_path("aaa").exists()
    assert session_path("bbb").exists()


def test_stores_are_reused_within_a_session():
    assert get_session_stores("aaa") is get_session_stores("aaa")


def test_session_writes_do_not_reach_the_corpus_store(tmp_path, monkeypatch):
    """The whole point of separate stores: a session upload is invisible to the
    corpus store even without any scope filter being applied."""
    monkeypatch.setattr(settings, "CHROMA_PATH", str(tmp_path / "corpus"))
    corpus = VectorStore()

    from tests.conftest import _stub_vector

    from app.shared.schemas.chunk import Chunk

    chunk = Chunk(
        chunk_id="s1",
        text="a session passage",
        source_doc="upload.pdf",
        chunk_type="text",
        extraction_method="text",
        corpus_scope=scope_for("aaa"),
    )
    session_vs, session_kw = get_session_stores("aaa")
    session_vs.upsert([(chunk, _stub_vector(chunk.text))])
    session_kw.rebuild()

    assert session_vs.count() == 1
    assert corpus.count() == 0


def test_bm25_statistics_are_per_store():
    """Separate stores mean a session's uploads cannot shift corpus IDF.

    The shared-index design filters scope after scoring, so this is the
    property separation buys.
    """
    _, keyword_a = get_session_stores("aaa")
    _, keyword_b = get_session_stores("bbb")

    assert keyword_a is not keyword_b
    assert len(keyword_a) == 0
    assert len(keyword_b) == 0


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

def test_drop_session_removes_the_directory():
    get_session_stores("aaa")
    assert session_path("aaa").exists()

    assert drop_session("aaa") is True
    assert not session_path("aaa").exists()


def test_drop_session_is_idempotent():
    assert drop_session("never-created") is False


def test_drop_session_leaves_other_sessions_alone():
    get_session_stores("aaa")
    get_session_stores("bbb")

    drop_session("aaa")

    assert not session_path("aaa").exists()
    assert session_path("bbb").exists()


def test_purge_removes_only_expired_sessions():
    get_session_stores("old")
    get_session_stores("fresh")

    stale = time.time() - 48 * 3600
    import os

    os.utime(session_path("old"), (stale, stale))
    session_store.reset_cache()

    removed = purge_expired(max_age_hours=24)

    assert removed == ["old"]
    assert not session_path("old").exists()
    assert session_path("fresh").exists()


def test_purge_ignores_unrecognised_directories():
    root = session_store.session_root()
    root.mkdir(parents=True, exist_ok=True)
    stranger = root / "not a session id"
    stranger.mkdir()
    stale = time.time() - 48 * 3600
    import os

    os.utime(stranger, (stale, stale))

    assert purge_expired(max_age_hours=24) == []
    assert stranger.exists()


# --------------------------------------------------------------------------- #
# Endpoint routing
# --------------------------------------------------------------------------- #

def test_upload_without_session_id_goes_to_the_corpus():
    client = TestClient(create_app())

    response = client.post(
        "/api/ingest",
        files={"files": ("report.pdf", io.BytesIO(_pdf_bytes("corpus text here")), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["corpus_scope"] == "persistent"


def test_upload_with_session_id_is_scoped_and_stays_out_of_the_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHROMA_PATH", str(tmp_path / "corpus"))

    from app.ingestion import indexer

    monkeypatch.setattr(indexer, "_vector_store", None)
    monkeypatch.setattr(indexer, "_keyword_index", None)

    client = TestClient(create_app())
    session_id = client.post("/api/session").json()["session_id"]

    response = client.post(
        "/api/ingest",
        files={"files": ("upload.pdf", io.BytesIO(_pdf_bytes("a user uploaded passage")), "application/pdf")},
        data={"session_id": session_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["corpus_scope"] == f"session:{session_id}"
    assert body["indexed"]["total"] > 0

    session_vs, _ = get_session_stores(session_id)
    assert session_vs.count() == body["indexed"]["total"]
    assert VectorStore().count() == 0


def test_upload_with_a_traversal_session_id_is_rejected():
    client = TestClient(create_app())

    response = client.post(
        "/api/ingest",
        files={"files": ("upload.pdf", io.BytesIO(_pdf_bytes("text")), "application/pdf")},
        data={"session_id": "../../chroma"},
    )

    assert response.status_code == 400


def test_delete_session_endpoint_drops_the_store():
    session_id = new_session_id()
    get_session_stores(session_id)
    client = TestClient(create_app())

    response = client.delete(f"/api/session/{session_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not session_path(session_id).exists()


def test_delete_unknown_session_reports_nothing_deleted():
    client = TestClient(create_app())
    response = client.delete(f"/api/session/{new_session_id()}")

    assert response.status_code == 200
    assert response.json()["deleted"] is False


# --------------------------------------------------------------------------- #
# Issued ids. Without authentication the id is the only thing protecting a
# session, so it must not be guessable.
# --------------------------------------------------------------------------- #

def test_issued_ids_are_unguessable_and_unique():
    ids = {new_session_id() for _ in range(200)}

    assert len(ids) == 200
    assert all(len(value) == 32 for value in ids)
    assert all(validate_issued_session_id(value) == value for value in ids)


@pytest.mark.parametrize(
    "session_id",
    ["demo1", "aaa", "sess42", "A" * 32, "0123456789abcdef", "g" * 32, "", "../x"],
)
def test_api_rejects_ids_it_did_not_issue(session_id):
    """A guessable id would let anyone write into or delete another session."""
    with pytest.raises(InvalidSessionId):
        validate_issued_session_id(session_id)


def test_create_session_endpoint_issues_a_usable_id():
    client = TestClient(create_app())
    response = client.post("/api/session")

    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert validate_issued_session_id(session_id) == session_id


def test_upload_with_a_guessable_session_id_is_rejected():
    client = TestClient(create_app())
    response = client.post(
        "/api/ingest",
        files={"files": ("upload.pdf", io.BytesIO(_pdf_bytes("text")), "application/pdf")},
        data={"session_id": "demo1"},
    )

    assert response.status_code == 400


def test_delete_with_a_guessable_session_id_is_rejected():
    client = TestClient(create_app())
    assert client.delete("/api/session/demo1").status_code == 400


# --------------------------------------------------------------------------- #
# Deletion is rename-then-remove, so it cannot leave a usable half-store
# --------------------------------------------------------------------------- #

def test_delete_leaves_no_half_removed_store(monkeypatch):
    """If removal fails after the rename, the session is still gone.

    A plain rmtree failing halfway would leave a Chroma directory that still
    opens and answers with whatever survived.
    """
    session_id = new_session_id()
    get_session_stores(session_id)

    monkeypatch.setattr(session_store.shutil, "rmtree", lambda *a, **k: None)

    assert drop_session(session_id) is True
    assert not session_path(session_id).exists()


def test_purge_collects_debris_from_a_failed_delete(monkeypatch):
    session_id = new_session_id()
    get_session_stores(session_id)

    real_rmtree = session_store.shutil.rmtree
    monkeypatch.setattr(session_store.shutil, "rmtree", lambda *a, **k: None)
    drop_session(session_id)

    debris = [p for p in session_store.session_root().iterdir() if p.name.startswith(".deleted-")]
    assert len(debris) == 1

    # Restore only rmtree: monkeypatch.undo() would also revert the fixture's
    # SESSION_STORE_ROOT override and point the sweep at the real directory.
    monkeypatch.setattr(session_store.shutil, "rmtree", real_rmtree)
    purge_expired(max_age_hours=24)

    assert not any(
        p.name.startswith(".deleted-") for p in session_store.session_root().iterdir()
    )


def test_delete_session_rejects_a_traversal_id():
    client = TestClient(create_app())
    response = client.delete("/api/session/..%2F..%2Fchroma")

    assert response.status_code in (400, 404)
