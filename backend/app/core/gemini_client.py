import logging
import time

from google import genai
from google.genai import types
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


# langchain's GoogleGenerativeAIEmbeddings issues one embedContent request per
# text (not a true batch call), so embed_batch() paces itself with this gap
# between requests to stay under the free-tier embedding ceiling (100/min) with
# headroom: 0.9s -> ~66 req/min. Tune here; the test suite sets it to 0.
_EMBED_REQUEST_INTERVAL_SEC = 0.9


# Vision-only SDK client options: a per-request cap and NO SDK-internal retry
# (default is 5 attempts with up to 60s backoff), so a wedged call drops to the
# OCR fallback (D-07) instead of the SDK's own retry loop grinding for minutes.
# generate() and embed_batch() build their clients separately and keep the SDK
# defaults.
#
# The cap is 60s, not the 15s used previously. A throttled key was the failure
# that 15s was meant to short-circuit, but throttling returns 429 immediately
# and is already handled by key rotation; the cap only ever fired on healthy
# calls. Transcribing one figure with _VISION_PROMPT measures ~30s, so 15s
# timed out every vision call and silently degraded every figure to OCR.
_VISION_HTTP_OPTIONS = types.HttpOptions(
    timeout=60_000,  # milliseconds
    retry_options=types.HttpRetryOptions(attempts=1),
)


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

    def _call_with_key_rotation(self, operation, *, max_retries: int | None = None):
        """Run ``operation(api_key)``; on a rate-limit error rotate to the next
        key and retry with exponential backoff.

        Up to ``max_retries`` additional attempts are made (falling back to
        ``self.max_retries`` when the argument is ``None``), so a call site that
        should fail fast — e.g. the vision path against a throttled key, which
        then falls back to OCR — can lower it without affecting the others.
        Non-rate-limit errors are re-raised on the spot; if every attempt is
        rate-limited the last such error is re-raised.
        """
        retries = self.max_retries if max_retries is None else max_retries
        attempts = retries + 1
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

    def generate_vision(
        self,
        stage: str,
        model: str,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        *,
        max_retries: int | None = None,
    ) -> str:
        """Send one image + text prompt to a Gemini vision model.

        Shares the key-rotation pool and rate-limit retry with :meth:`generate`
        via :meth:`_call_with_key_rotation`; ``max_retries`` overrides the
        rotation retry budget for this call only (the vision path passes a low
        value so it fails fast to OCR when the key is throttled). Usage is
        recorded when the response carries token counts. Returns the model's
        text (``""`` if it returned none — the caller decides usability).
        """

        def _operation(api_key: str) -> str:
            client = genai.Client(api_key=api_key, http_options=_VISION_HTTP_OPTIONS)
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
            )

            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                self.tracker.record(
                    stage=stage,
                    model=model,
                    prompt_tokens=usage.prompt_token_count or 0,
                    output_tokens=usage.candidates_token_count or 0,
                )
            return response.text or ""

        return self._call_with_key_rotation(_operation, max_retries=max_retries)

    def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order.

        Shares the key-rotation pool and rate-limit retry with :meth:`generate`.
        Because the underlying API is one request per text, this sleeps
        ``_EMBED_REQUEST_INTERVAL_SEC`` between requests to stay under the
        free-tier per-minute cap. The embeddings API returns no token counts, so
        — per D-32: record real numbers or none — no usage is recorded here.
        """
        if not texts:
            return []

        model_id = model if model.startswith("models/") else f"models/{model}"

        def _operation(api_key: str) -> list[list[float]]:
            embeddings = GoogleGenerativeAIEmbeddings(
                model=model_id, google_api_key=api_key
            )
            vectors: list[list[float]] = []
            for i, text in enumerate(texts):
                if i and _EMBED_REQUEST_INTERVAL_SEC > 0:
                    time.sleep(_EMBED_REQUEST_INTERVAL_SEC)
                vectors.extend(embeddings.embed_documents([text]))
            return vectors

        return self._call_with_key_rotation(_operation)
