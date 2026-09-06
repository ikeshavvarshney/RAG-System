import uuid
from unittest.mock import patch

import pytest

from app.core import gemini_client as gc
from app.core.config import settings
from app.core.gemini_client import GeminiClient
from app.core.key_rotation import KeyRotator
from app.ingestion import embedder
from app.ingestion.embedder import embed_chunks, embed_query
from app.shared.schemas.chunk import Chunk

DIM = 768


def _chunk(text: str) -> Chunk:
    return Chunk(
        chunk_id=str(uuid.uuid4()),
        text=text,
        source_doc="doc.pdf",
        chunk_type="text",
        extraction_method="text",
        corpus_scope="persistent",
    )


def _fake_embed_documents(texts):
    # Deterministic per text so cache round-trips can be compared for equality.
    return [[round(len(t) * 0.01, 4)] * DIM for t in texts]


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_DIR", str(tmp_path))
    # Keep retry backoff instant and the key pool deterministic for every test.
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    monkeypatch.setattr(embedder, "_client", GeminiClient(backoff_base=0))
    return tmp_path


@pytest.fixture
def mock_embeddings():
    with patch("app.core.gemini_client.GoogleGenerativeAIEmbeddings") as mock_cls:
        mock_cls.return_value.embed_documents.side_effect = _fake_embed_documents
        yield mock_cls.return_value


def test_batch_of_50_chunks_embeds_each_unique_text_once(isolated_cache, mock_embeddings):
    chunks = [_chunk(f"distinct chunk body number {i}") for i in range(50)]

    out = embed_chunks(chunks)

    assert len(out) == 50
    # One embedContent request per unique text (the API is not a true batch).
    assert mock_embeddings.embed_documents.call_count == 50
    assert all(len(vec) == DIM for _, vec in out)


def test_every_processed_chunk_has_embedding_model_set(isolated_cache, mock_embeddings):
    out = embed_chunks([_chunk("alpha"), _chunk("beta")])

    assert [c.embedding_model for c, _ in out] == [settings.EMBEDDING_MODEL] * 2
    # dense_vector_id stays unset here - that's the vector store's job.
    assert all(c.dense_vector_id is None for c, _ in out)


def test_returned_vectors_have_expected_dimensionality(isolated_cache, mock_embeddings):
    out = embed_chunks([_chunk("gamma")])

    assert len(out) == 1
    _, vector = out[0]
    assert len(vector) == DIM
    assert all(isinstance(x, float) for x in vector)


def test_input_chunks_are_not_mutated(isolated_cache, mock_embeddings):
    original = _chunk("delta")
    embed_chunks([original])

    assert original.embedding_model is None  # a copy was returned, input untouched


def test_batching_splits_when_over_batch_size(isolated_cache, mock_embeddings, monkeypatch):
    monkeypatch.setattr(embedder, "EMBED_BATCH_SIZE", 10)
    chunks = [_chunk(f"row {i}") for i in range(25)]

    batch_sizes: list[int] = []
    real_embed_batch = embedder._client.embed_batch
    monkeypatch.setattr(
        embedder._client,
        "embed_batch",
        lambda texts, model: (batch_sizes.append(len(texts)), real_embed_batch(texts, model))[1],
    )

    out = embed_chunks(chunks)

    assert len(out) == 25
    assert batch_sizes == [10, 10, 5]  # _embed_texts still slices by EMBED_BATCH_SIZE
    assert mock_embeddings.embed_documents.call_count == 25  # one request per text


def test_duplicate_texts_are_embedded_once(isolated_cache, mock_embeddings):
    chunks = [_chunk("same text") for _ in range(8)]

    out = embed_chunks(chunks)

    assert len(out) == 8
    assert mock_embeddings.embed_documents.call_count == 1
    sent = mock_embeddings.embed_documents.call_args.args[0]
    assert sent == ["same text"]
    # every duplicate still gets its vector back
    assert all(vec == out[0][1] for _, vec in out)


def test_cache_hit_issues_zero_additional_calls(isolated_cache, mock_embeddings):
    chunks = [_chunk("cacheable content one"), _chunk("cacheable content two")]

    first = embed_chunks(chunks)
    assert mock_embeddings.embed_documents.call_count == 2  # one per unique text

    second = embed_chunks([_chunk("cacheable content one"), _chunk("cacheable content two")])

    assert mock_embeddings.embed_documents.call_count == 2  # served entirely from disk
    assert [v for _, v in first] == [v for _, v in second]


def test_partial_cache_only_embeds_the_misses(isolated_cache, mock_embeddings):
    embed_chunks([_chunk("warm one"), _chunk("warm two")])
    assert mock_embeddings.embed_documents.call_count == 2

    embed_chunks([_chunk("warm one"), _chunk("cold three"), _chunk("warm two")])

    assert mock_embeddings.embed_documents.call_count == 3  # only the miss
    assert mock_embeddings.embed_documents.call_args.args[0] == ["cold three"]


def test_embed_query_works_independently(isolated_cache, mock_embeddings):
    vector = embed_query("a standalone query string")

    assert len(vector) == DIM
    assert mock_embeddings.embed_documents.call_count == 1


def test_embed_query_shares_cache_keying_with_embed_chunks(isolated_cache, mock_embeddings):
    shared = "content that appears as both a chunk and a query"

    (_, chunk_vector), = embed_chunks([_chunk(shared)])
    assert mock_embeddings.embed_documents.call_count == 1

    query_vector = embed_query(shared)

    assert mock_embeddings.embed_documents.call_count == 1  # cache hit, no new call
    assert query_vector == chunk_vector


def test_embed_query_result_is_cached_for_later_chunks(isolated_cache, mock_embeddings):
    shared = "query first, chunk later"

    q = embed_query(shared)
    assert mock_embeddings.embed_documents.call_count == 1

    (_, c), = embed_chunks([_chunk(shared)])

    assert mock_embeddings.embed_documents.call_count == 1
    assert c == q


def test_empty_inputs(isolated_cache, mock_embeddings):
    assert embed_chunks([]) == []
    mock_embeddings.embed_documents.assert_not_called()


def test_rate_limit_triggers_rotation_and_retry_not_raise(isolated_cache):
    attempts = {"n": 0}
    used_keys: list[str] = []

    class FakeEmbeddings:
        def __init__(self, *, model, google_api_key):
            used_keys.append(google_api_key)

        def embed_documents(self, texts):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("429 Too Many Requests: quota exceeded")
            return _fake_embed_documents(texts)

    with patch.object(gc, "GoogleGenerativeAIEmbeddings", FakeEmbeddings):
        out = embed_chunks([_chunk("needs a retry"), _chunk("second text")])

    # per-text loop: text1 fails (n=1) -> whole _operation retried on key-b ->
    # text1 ok (n=2), text2 ok (n=3).
    assert attempts["n"] == 3
    assert used_keys == ["key-a", "key-b"]  # rotated to the next key
    assert len(out) == 2 and all(len(v) == DIM for _, v in out)
