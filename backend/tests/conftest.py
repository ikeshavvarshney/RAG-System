import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clear_key_pool_env(monkeypatch):
    """Ensure tests never depend on a developer's real .env key pools."""
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)