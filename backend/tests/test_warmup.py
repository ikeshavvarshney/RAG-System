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


def test_lifespan_loads_the_reranker_once_through_the_shared_loader(monkeypatch, caplog):
    import sys
    import types

    from app.core import warmup
    from app.ingestion import embedder
    from app.query import llm, rerank

    loaded = threading.Event()
    constructed: list[tuple] = []

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))
            loaded.set()

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=FakeCrossEncoder))
    monkeypatch.setattr(rerank, "_model", None)
    monkeypatch.setattr(llm, "warm_up", lambda: None)
    monkeypatch.setattr(embedder, "warm_up", lambda: None)
    monkeypatch.setattr(main, "warm_up_clients", warmup.warm_up_clients)  # undo the autouse no-op

    with caplog.at_level(logging.INFO, logger="app.query.rerank"):
        with TestClient(main.create_app()) as client:
            assert loaded.wait(timeout=5)
            assert client.get("/api/health").status_code == 200
            first = rerank._load_model()  # a query arriving now reuses the same instance
            assert rerank._load_model() is first

    assert len(constructed) == 1
    assert "loaded in" in caplog.text


def test_reranker_failure_does_not_stop_startup_or_other_warm_up_steps(monkeypatch, caplog):
    from app.core import warmup
    from app.ingestion import embedder
    from app.query import llm, rerank

    reached = threading.Event()

    def failing_load():
        reached.set()
        raise OSError("hub unreachable")

    monkeypatch.setattr(llm, "warm_up", lambda: (_ for _ in ()).throw(RuntimeError("no keys")))
    monkeypatch.setattr(embedder, "warm_up", lambda: None)
    monkeypatch.setattr(rerank, "warm_up", failing_load)
    monkeypatch.setattr(main, "warm_up_clients", warmup.warm_up_clients)

    with caplog.at_level(logging.WARNING):
        with TestClient(main.create_app()) as client:
            assert reached.wait(timeout=5)  # the reranker step still ran after the client step failed
            assert client.get("/api/health").status_code == 200

    assert "generation client warm-up failed" in caplog.text and "reranker warm-up failed" in caplog.text
