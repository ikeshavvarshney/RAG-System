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


def _stub_vector(text: str, dim: int = 8) -> list[float]:
    """Deterministic, cheap, non-zero embedding stand-in."""
    seed = sum(bytearray(text.encode("utf-8"))) % 97 or 1
    return [(seed + i) / 100.0 for i in range(dim)]


@pytest.fixture(autouse=True)
def isolate_index_stores(tmp_path, monkeypatch):
    """Keep every test off the real Gemini API and the real ./data/chroma.

    ``pipeline.ingest_files`` now runs the indexer as its final stage, so any
    test that ingests would otherwise embed for real and write to the on-disk
    Chroma. This points CHROMA_PATH at a per-test temp dir, resets the indexer
    singletons, and swaps ``indexer.embed_chunks`` for a deterministic stub.
    Tests that assert on indexing build their own VectorStore/KeywordIndex
    against this same CHROMA_PATH.
    """
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