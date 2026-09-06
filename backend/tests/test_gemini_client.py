from unittest.mock import MagicMock, patch

import pytest

from app.core import gemini_client as gc
from app.core.gemini_client import GeminiClient, _is_rate_limit_error
from app.core.key_rotation import KeyRotator
from app.core.usage import UsageTracker


def test_generate_records_usage_from_response_metadata():
    tracker = UsageTracker()
    client = GeminiClient(tracker=tracker)

    mock_response = MagicMock()
    mock_response.text = "mocked response"
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 5

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        result = client.generate(stage="test_stage", model="gemini-3.6-flash", prompt="hi")

    assert result == "mocked response"
    assert tracker.total_tokens() == 15
    assert tracker.by_stage() == {"test_stage": 15}


def test_multiple_calls_sum_correctly_by_stage():
    tracker = UsageTracker()
    client = GeminiClient(tracker=tracker)

    mock_response = MagicMock()
    mock_response.text = "mocked"
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 10

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        client.generate(stage="stage_a", model="gemini-3.6-flash", prompt="hi")
        client.generate(stage="stage_a", model="gemini-3.6-flash", prompt="hi")
        client.generate(stage="stage_b", model="gemini-3.6-flash", prompt="hi")

    assert tracker.total_tokens() == 60
    assert tracker.by_stage() == {"stage_a": 40, "stage_b": 20}


# --------------------------------------------------------------------------- #
# Rate-limit detection + key rotation / retry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "message",
    [
        "429 Too Many Requests",
        "google.api_core.exceptions.ResourceExhausted: 429 quota exceeded",
        "RATE LIMIT reached for text-embedding-004",
    ],
)
def test_is_rate_limit_error_recognises_common_shapes(message):
    assert _is_rate_limit_error(RuntimeError(message))


def test_is_rate_limit_error_ignores_unrelated_errors():
    assert not _is_rate_limit_error(ValueError("bad prompt: unsupported content"))


def test_generate_rotates_key_and_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    client = GeminiClient(backoff_base=0)

    ok = MagicMock()
    ok.text = "recovered"
    ok.usage_metadata.prompt_token_count = 2
    ok.usage_metadata.candidates_token_count = 3

    outcomes = [RuntimeError("429 RESOURCE_EXHAUSTED"), ok]

    def fake_generate_content(*args, **kwargs):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.side_effect = (
            fake_generate_content
        )
        result = client.generate(stage="s", model="m", prompt="p")

    assert result == "recovered"
    assert outcomes == []  # both the failure and the success were consumed


def test_generate_does_not_retry_non_rate_limit_errors(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    client = GeminiClient(backoff_base=0)

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.side_effect = ValueError(
            "bad prompt"
        )
        with pytest.raises(ValueError):
            client.generate(stage="s", model="m", prompt="p")


def test_generate_reraises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b"))
    client = GeminiClient(max_retries=2, backoff_base=0)

    with patch("app.core.gemini_client.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.side_effect = RuntimeError(
            "429 quota"
        )
        with pytest.raises(RuntimeError, match="429"):
            client.generate(stage="s", model="m", prompt="p")

    assert mock_client_cls.return_value.models.generate_content.call_count == 3


# --------------------------------------------------------------------------- #
# embed_batch
# --------------------------------------------------------------------------- #
def test_embed_batch_returns_one_vector_per_text(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a"))
    client = GeminiClient(backoff_base=0)

    with patch("app.core.gemini_client.GoogleGenerativeAIEmbeddings") as mock_emb_cls:
        mock_emb_cls.return_value.embed_documents.side_effect = lambda texts: [
            [0.1] * 768 for _ in texts
        ]
        vectors = client.embed_batch(["a", "b", "c"], model="text-embedding-004")

    assert len(vectors) == 3
    assert all(len(v) == 768 for v in vectors)
    # model id normalised to the "models/..." form the SDK expects.
    assert mock_emb_cls.call_args.kwargs["model"] == "models/text-embedding-004"


def test_embed_batch_empty_input_makes_no_call(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a"))
    client = GeminiClient(backoff_base=0)

    with patch("app.core.gemini_client.GoogleGenerativeAIEmbeddings") as mock_emb_cls:
        assert client.embed_batch([], model="text-embedding-004") == []
        mock_emb_cls.assert_not_called()


def test_embed_batch_rotates_key_and_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a,key-b,key-c"))
    client = GeminiClient(backoff_base=0)

    used_keys: list[str] = []
    attempts = {"n": 0}

    class FakeEmbeddings:
        def __init__(self, *, model, google_api_key):
            used_keys.append(google_api_key)

        def embed_documents(self, texts):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(gc, "GoogleGenerativeAIEmbeddings", FakeEmbeddings)

    vectors = client.embed_batch(["x", "y"], model="text-embedding-004")

    # per-text: 1st request fails -> whole _operation retried on key-b -> then
    # both texts embed (n reaches 3).
    assert attempts["n"] == 3
    assert used_keys == ["key-a", "key-b"]  # rotated to the next key on retry
    assert len(vectors) == 2 and len(vectors[0]) == 768


def test_embed_batch_paces_between_requests(monkeypatch):
    monkeypatch.setattr(gc, "gemini_keys", KeyRotator("key-a"))
    monkeypatch.setattr(gc, "_EMBED_REQUEST_INTERVAL_SEC", 0.01)
    slept: list[float] = []
    monkeypatch.setattr(gc.time, "sleep", lambda s: slept.append(s))

    with patch("app.core.gemini_client.GoogleGenerativeAIEmbeddings") as mock_emb_cls:
        mock_emb_cls.return_value.embed_documents.side_effect = lambda texts: [
            [0.0] * 4 for _ in texts
        ]
        vectors = GeminiClient(backoff_base=0).embed_batch(
            ["a", "b", "c", "d"], model="gemini-embedding-001"
        )

    assert len(vectors) == 4
    assert mock_emb_cls.return_value.embed_documents.call_count == 4  # one per text
    assert slept == [0.01, 0.01, 0.01]  # 3 gaps between 4 requests