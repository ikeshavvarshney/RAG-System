import logging
import time

from google import genai
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.key_rotation import gemini_keys
from app.core.usage import UsageTracker

logger = logging.getLogger(__name__)

# Substrings that identify a rate-limit / quota response across the various
# exception types the Gemini SDK and its transports can raise.
_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "ratelimit",
    "resource_exhausted",
    "resourceexhausted",
    "quota",
    "too many requests",
)


def _is_rate_limit_error(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _RATE_LIMIT_MARKERS)


class GeminiClient:
    """Single wrapper owning key rotation + usage recording for every
    Gemini call. A call that bypasses this wrapper bypasses both —
    that's obvious in review, and is the whole point of D-32.

    Every outbound call goes through :meth:`_call_with_key_rotation`, which
    rotates to the next key in the pool and retries with exponential backoff
    when the API reports a rate limit; any other error propagates immediately.
    """

    def __init__(
        self,
        tracker: UsageTracker | None = None,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ):
        self.tracker = tracker if tracker is not None else UsageTracker()
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def _call_with_key_rotation(self, operation):
        """Run ``operation(api_key)``; on a rate-limit error rotate to the next
        key and retry with exponential backoff.

        Up to ``max_retries`` additional attempts are made. Non-rate-limit
        errors are re-raised on the spot; if every attempt is rate-limited the
        last such error is re-raised.
        """
        attempts = self.max_retries + 1
        last_exc: BaseException | None = None

        for attempt in range(attempts):
            api_key = gemini_keys.next()
            try:
                return operation(api_key)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                last_exc = exc
                logger.warning(
                    "Gemini rate-limited (attempt %d/%d); rotating key and retrying",
                    attempt + 1,
                    attempts,
                )
                if attempt < attempts - 1 and self.backoff_base > 0:
                    time.sleep(self.backoff_base * (2**attempt))

        assert last_exc is not None  # loop ran at least once
        raise last_exc

    def generate(self, stage: str, model: str, prompt: str) -> str:
        def _operation(api_key: str) -> str:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(model=model, contents=prompt)

            usage = response.usage_metadata
            self.tracker.record(
                stage=stage,
                model=model,
                prompt_tokens=usage.prompt_token_count,
                output_tokens=usage.candidates_token_count,
            )
            return response.text

        return self._call_with_key_rotation(_operation)

    def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order.

        Shares the key-rotation pool and rate-limit retry with :meth:`generate`.
        The embeddings API returns no token counts, so — per D-32: record real
        numbers or none — no usage is recorded here.
        """
        if not texts:
            return []

        model_id = model if model.startswith("models/") else f"models/{model}"

        def _operation(api_key: str) -> list[list[float]]:
            embeddings = GoogleGenerativeAIEmbeddings(
                model=model_id, google_api_key=api_key
            )
            return embeddings.embed_documents(texts)

        return self._call_with_key_rotation(_operation)
