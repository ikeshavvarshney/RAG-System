import logging
import threading
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import main
from app.core import gemini_client as gc
from app.core.gemini_client import GeminiClient
from app.core.key_rotation import KeyRotator


def test_startup_succeeds_when_warm_up_fails(monkeypatch, caplog):
    attempted = threading.Event()

    def failing_warm_up():
        attempted.set()
        raise RuntimeError("no network")

    monkeypatch.setattr(main, "warm_up_clients", failing_warm_up)

    with caplog.at_level(logging.WARNING):
        with TestClient(main.create_app()) as client:
            assert attempted.wait(timeout=5)
            assert client.get("/api/health").status_code == 200

    assert "client warm-up failed" in caplog.text


def test_startup_does_not_wait_for_warm_up(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_warm_up():
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr(main, "warm_up_clients", slow_warm_up)

    begun = time.monotonic()
    with TestClient(main.create_app()) as client:
        assert time.monotonic() - begun < 5
        assert started.wait(timeout=5)
        assert client.get("/api/health").status_code == 200
        release.set()


def test_warm_up_builds_a_client_and_embedder_per_key(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    client = GeminiClient(backoff_base=0)

    with patch("app.core.gemini_client.genai.Client") as sdk_cls, patch(
        "app.core.gemini_client.GoogleGenerativeAIEmbeddings"
    ) as emb_cls:
        client.warm_up_generation()
        client.warm_up_embeddings("models/m")

        assert [c.kwargs["api_key"] for c in sdk_cls.call_args_list] == ["key-a", "key-b"]
        assert [c.kwargs["google_api_key"] for c in emb_cls.call_args_list] == ["key-a", "key-b"]

        response = MagicMock(text="ok")
        sdk_cls.return_value.models.generate_content.return_value = response
        client.generate(stage="s", model="m", prompt="p")
        client.generate(stage="s", model="m", prompt="p")

        assert sdk_cls.call_count == 2
