import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.query import cache
from app.shared import session_store
from app.shared.schemas.citation import CorpusCitation
from app.shared.session_store import new_session_id, scope_for


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_STORE_ROOT", str(tmp_path / "sessions"))
    session_store.reset_cache()
    monkeypatch.setattr(cache, "embed_queries", lambda texts: [[1.0, 0.0] for _ in texts])
    yield
    session_store.reset_cache()


def _seed(question: str, scope: str, doc: str) -> None:
    citations = [CorpusCitation(source_doc=doc, page=1, chunk_id="c1")]
    cache.put(question, question, "answer", citations, scope)


def test_deleting_a_document_drops_only_cache_entries_citing_it():
    sid = new_session_id()
    scope = scope_for(sid)
    _seed("q about a", scope, "a.pdf")
    _seed("q about b", scope, "b.pdf")

    response = TestClient(create_app()).delete(f"/api/session/{sid}/documents/a.pdf")

    assert response.status_code == 200
    assert response.json()["invalidated_cache_entries"] == 1
    assert cache.get_answer_cache().count() == 1


def test_deleting_a_document_leaves_other_scopes_alone():
    sid = new_session_id()
    _seed("q about a", scope_for(sid), "a.pdf")
    _seed("q about a", "persistent", "a.pdf")

    TestClient(create_app()).delete(f"/api/session/{sid}/documents/a.pdf")

    assert cache.lookup("q about a", "persistent") is not None
    assert cache.lookup("q about a", scope_for(sid)) is None


def test_deleting_a_session_drops_every_cache_entry_in_its_scope():
    sid = new_session_id()
    _seed("q about a", scope_for(sid), "a.pdf")
    _seed("q about b", scope_for(sid), "b.pdf")
    _seed("q about a", "persistent", "a.pdf")

    response = TestClient(create_app()).delete(f"/api/session/{sid}")

    assert response.json()["invalidated_cache_entries"] == 2
    assert cache.get_answer_cache().count() == 1
