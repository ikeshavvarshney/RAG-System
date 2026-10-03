import socket
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clear_key_pool_env(monkeypatch):
    """Ensure tests never depend on a developer's real .env key pools."""
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)


@pytest.fixture(autouse=True)
def no_real_web_search(monkeypatch):
    """The module-level Tavily rotator is built from .env at import, so without this a test could hold a real key."""
    from app.core.key_rotation import KeyRotator
    from app.query import web_search

    monkeypatch.setattr(web_search, "tavily_keys", KeyRotator(""))


@pytest.fixture(autouse=True)
def no_embed_pacing(monkeypatch):
    """Zero the embed-request pacing sleep so tests never actually wait on it."""
    from app.core import gemini_client

    monkeypatch.setattr(gemini_client, "_EMBED_REQUEST_INTERVAL_SEC", 0.0, raising=False)


def _stub_vector(text: str, dim: int = 8) -> list[float]:
    """Deterministic, cheap, non-zero embedding stand-in."""
    seed = sum(bytearray(text.encode("utf-8"))) % 97 or 1
    return [(seed + i) / 100.0 for i in range(dim)]


class _LengthCrossEncoder:
    """Stand-in for the real cross-encoder: scores shorter passages higher."""

    def predict(self, pairs):
        return [-float(len(text)) for _, text in pairs]


@pytest.fixture(autouse=True)
def fake_cross_encoder(monkeypatch):
    from app.query import rerank

    monkeypatch.setattr(rerank, "_model", _LengthCrossEncoder())


@pytest.fixture(autouse=True)
def isolate_index_stores(tmp_path, monkeypatch):
    """Keep every test off the real Gemini API and the real ./data/chroma."""
    from app.ingestion import indexer

    monkeypatch.setattr(settings, "CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(indexer, "_vector_store", None)
    monkeypatch.setattr(indexer, "_keyword_index", None)

    def _stub_embed_chunks(chunks):
        return [
            (c.model_copy(update={"embedding_model": "stub-embed-001"}), _stub_vector(c.text))
            for c in chunks
        ]

    monkeypatch.setattr(indexer, "embed_chunks", _stub_embed_chunks)

class FakeLLM:
    """Scripted stand-in for ``llm.generate``, keyed by stage name."""

    def __init__(self):
        self.replies: dict[str, str | Exception | Callable[[str], str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.token_caps: list[int] = []
        self.models: list[str | None] = []

    async def __call__(
        self, stage: str, prompt: str, max_output_tokens: int, model: str | None = None
    ) -> str:
        self.calls.append((stage, prompt))
        self.token_caps.append(max_output_tokens)
        self.models.append(model)
        reply = self.replies.get(stage)
        if reply is None:
            raise RuntimeError(f"unscripted LLM call: {stage}")
        if isinstance(reply, Exception):
            raise reply
        return reply(prompt) if callable(reply) else reply


@pytest.fixture
def fake_llm(monkeypatch):
    from app.query import llm

    fake = FakeLLM()
    monkeypatch.setattr(llm, "generate", fake)
    return fake


@pytest.fixture(autouse=True)
def no_startup_warm_up(monkeypatch):
    monkeypatch.setattr("app.main.warm_up_clients", lambda: None)


@pytest.fixture(autouse=True)
def fresh_query_state(tmp_path, monkeypatch):
    from app.query import cache, history

    monkeypatch.setattr(history, "_store", history.HistoryStore())
    monkeypatch.setattr(settings, "CACHE_PATH", str(tmp_path / "answer_cache"))
    monkeypatch.setattr(cache, "_cache", None)
    yield
    if cache._cache is not None:
        cache._cache.close()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail loudly if any test resolves a non-local host, even when product code swallows the error."""
    real_getaddrinfo = socket.getaddrinfo
    attempts: list[str] = []

    def guarded(host, *args, **kwargs):
        if str(host).lower() not in {"localhost", "127.0.0.1", "::1", "testserver", ""}:
            attempts.append(str(host))
            raise OSError(f"test attempted a real network call to {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    yield
    if attempts:
        pytest.fail(f"real network access attempted: {sorted(set(attempts))}", pytrace=False)


@pytest.fixture(autouse=True)
def stub_generation(monkeypatch):
    """Pipeline tests never call the real answer model. test_query_generation imports
    ``generate_answer`` from app.query.generation directly, so it still exercises the real one."""
    from app.query import generation, pipeline

    async def _stub(question, passages=None, *, sub_contexts=None, stage=None):
        _, refs = generation.build_prompt(question, passages, sub_contexts)
        if not refs:
            return generation.GenerationResult(answer=generation.NOT_IN_CONTEXT, is_non_answer=True)
        return generation.GenerationResult(
            answer="Stub answer [1].", cited_markers=[1], passages=refs
        )

    monkeypatch.setattr(pipeline, "generate_answer", _stub)
