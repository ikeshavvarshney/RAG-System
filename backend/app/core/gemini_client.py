import collections
import logging
import threading
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.key_rotation import call_with_key_rotation, gemini_keys
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


def _is_daily_quota_error(exc: BaseException) -> bool:
    return "perday" in str(exc).lower()


def minimal_thinking_config(max_output_tokens: int | None = None) -> types.GenerateContentConfig:
    # MINIMAL, not budget 0: the Lite models reject a zero budget.
    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        max_output_tokens=max_output_tokens,
    )


# A key out of daily quota stays out until midnight Pacific; re-probe hourly.
_DAILY_QUOTA_BLOCK_SEC = 3600


# langchain's GoogleGenerativeAIEmbeddings issues one embedContent request per
# text (not a true batch call), so embed_batch() paces itself with this gap
# between requests to stay under the free-tier embedding ceiling (100/min) with
# headroom: 0.9s -> ~66 req/min. Tune here; the test suite sets it to 0.
_EMBED_REQUEST_INTERVAL_SEC = 0.9


# Bounds every text-model request, connect included, so an unreachable network fails in two minutes instead of
# waiting out the OS connect timeout once per resolved address (about 170 s seen). `client_args` timeouts do not
# work here: the SDK passes a per-request timeout of None, which httpx treats as "no timeout" and which overrides
# the client's own. HttpOptions.timeout is applied per request, but it covers every phase, so connect cannot be
# bounded separately.
_TEXT_HTTP_OPTIONS = types.HttpOptions(timeout=120_000)  # milliseconds

# Vision calls carry an image, so they get their own bounds. Three numbers decide how long one attempt can take:
#   VISION_CONNECT_TIMEOUT_SEC  one TCP connect. Python tries every address a name resolves to, one after another,
#                               each with the full timeout, so a blackholed network used to add the timeouts up: eight
#                               addresses at the OS limit of about 21 s each is the 170 to 180 s we saw per call.
#   VISION_CALL_TIMEOUT_SEC     the whole attempt, connect through the end of the reply. A hard deadline: per-phase
#                               HTTP timeouts alone cannot bound a call that is slow in several phases or across
#                               several addresses.
#   VISION_SLOW_CALL_WARN_SEC   a finished call slower than this is logged as a warning.
# One attempt, no SDK retry (a stalled call drops to the retry below, then to OCR, D-07).
VISION_CONNECT_TIMEOUT_SEC = 10.0
VISION_CALL_TIMEOUT_SEC = 60.0
VISION_SLOW_CALL_WARN_SEC = 30.0


class CallDeadlineExceeded(httpx.TimeoutException):
    """One attempt used up VISION_CALL_TIMEOUT_SEC."""


_ssl_context = None
_ssl_lock = threading.Lock()


def _shared_ssl_context():
    """One SSL context for every vision client: loading the CA bundle costs over a second each time."""
    global _ssl_context
    with _ssl_lock:
        if _ssl_context is None:
            _ssl_context = httpx.create_ssl_context()
        return _ssl_context


class BoundedHttpClient(httpx.Client):
    """An httpx client whose requests always use the vision timeouts.

    The SDK passes its own scalar timeout with every request, which would set the connect phase to the whole
    budget. Replacing it at build time keeps the connect limit short.
    """

    def __init__(self, *, connect_timeout: float, call_timeout: float, **kwargs):
        kwargs.setdefault("follow_redirects", True)  # what the SDK's own client does
        kwargs.setdefault("verify", _shared_ssl_context())
        super().__init__(**kwargs)
        self._bounds = httpx.Timeout(connect=connect_timeout, read=call_timeout, write=call_timeout, pool=connect_timeout)

    def build_request(self, *args, **kwargs):
        kwargs["timeout"] = self._bounds
        return super().build_request(*args, **kwargs)


def _call_with_deadline(call, seconds: float):
    """Run ``call`` and give up waiting after ``seconds``. A call that never finishes is left on a daemon thread,
    which its own socket timeouts end soon after."""
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=run, name="vision-call", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise CallDeadlineExceeded(f"the call did not finish within {seconds:.0f}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


# A vision call that hits a dropped or stalled connection is retried this many times, waiting these seconds before
# each retry (so the number of retries is the number of delays). Quota, rate-limit and auth errors are never retried
# here: rotation handles a 429, and a 401 or 403 means the key itself is wrong.
TRANSIENT_RETRY_DELAYS_SEC = (2.0, 5.0, 10.0)
TRANSIENT_MAX_RETRIES = len(TRANSIENT_RETRY_DELAYS_SEC)
_TRANSIENT_NETWORK_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.TimeoutException,  # read, connect, write, pool timeouts and CallDeadlineExceeded
)


def is_transient_error(exc: BaseException) -> bool:
    """A network failure or an HTTP 5xx: worth retrying. Anything else (4xx, bad request, quota) is not."""
    if isinstance(exc, _TRANSIENT_NETWORK_ERRORS):
        return True
    return isinstance(exc, genai_errors.APIError) and isinstance(exc.code, int) and 500 <= exc.code < 600


class GeminiClient:
    """Single wrapper owning key rotation + usage recording for every Gemini call."""

    def __init__(
        self,
        tracker: UsageTracker | None = None,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        transient_delays: tuple[float, ...] = TRANSIENT_RETRY_DELAYS_SEC,
    ):
        self.tracker = tracker if tracker is not None else UsageTracker()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.transient_delays = transient_delays
        # Overrides the API host for vision calls (a proxy, or a test pointing at a blackhole address).
        self.vision_base_url: str | None = None
        # One SDK client, with its own connection pool, per API key, reused by every vision call with that key.
        self._vision_clients: dict[str, tuple[genai.Client, BoundedHttpClient]] = {}
        # Transient retries made so far, by exception type, for run summaries.
        self.transient_retries: collections.Counter[str] = collections.Counter()
        # Constructing an SDK client costs seconds, so build one per key and reuse it.
        self._clients: dict[str, genai.Client] = {}
        self._embedders: dict[tuple[str, str], GoogleGenerativeAIEmbeddings] = {}
        self._cache_lock = threading.Lock()

    def _client_for(self, api_key: str) -> genai.Client:
        with self._cache_lock:
            cached = self._clients.get(api_key)
        if cached is not None:
            return cached
        built = genai.Client(api_key=api_key, http_options=_TEXT_HTTP_OPTIONS)  # slow, so built outside the lock
        with self._cache_lock:
            return self._clients.setdefault(api_key, built)

    def _vision_client_for(self, api_key: str) -> genai.Client:
        with self._cache_lock:
            cached = self._vision_clients.get(api_key)
        if cached is not None:
            return cached[0]
        http = BoundedHttpClient(connect_timeout=VISION_CONNECT_TIMEOUT_SEC, call_timeout=VISION_CALL_TIMEOUT_SEC)
        built = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                base_url=self.vision_base_url,
                timeout=int(VISION_CALL_TIMEOUT_SEC * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_client=http,
            ),
        )
        with self._cache_lock:
            existing = self._vision_clients.setdefault(api_key, (built, http))
        if existing[1] is not http:
            http.close()
        return existing[0]

    def _drop_vision_client(self, api_key: str) -> None:
        """Forget a client whose connection may be stale or stalled, so the next attempt opens a fresh one."""
        with self._cache_lock:
            dropped = self._vision_clients.pop(api_key, None)
        if dropped is not None:
            # Closing waits for a request stuck in a connect, which is the very case that got us here, so it must
            # not hold up the caller.
            threading.Thread(target=self._close_quietly, args=(dropped[1],), name="vision-close", daemon=True).start()

    @staticmethod
    def _close_quietly(http: "BoundedHttpClient") -> None:
        try:
            http.close()
        except Exception:  # noqa: BLE001 - closing a broken connection may raise; the client is discarded anyway
            pass

    def _embedder_for(self, api_key: str, model_id: str) -> GoogleGenerativeAIEmbeddings:
        with self._cache_lock:
            cached = self._embedders.get((api_key, model_id))
        if cached is not None:
            return cached
        built = GoogleGenerativeAIEmbeddings(model=model_id, google_api_key=api_key)
        with self._cache_lock:
            return self._embedders.setdefault((api_key, model_id), built)

    def warm_up_generation(self) -> None:
        for api_key in gemini_keys.keys:
            self._client_for(api_key)

    def warm_up_embeddings(self, model_id: str) -> None:
        for api_key in gemini_keys.keys:
            self._embedder_for(api_key, model_id)

    def _call_with_key_rotation(
        self, operation, *, scope: str = "", max_retries: int | None = None
    ):
        return call_with_key_rotation(
            operation,
            gemini_keys,
            scope=scope,
            retries=self.max_retries if max_retries is None else max_retries,
            backoff_base=self.backoff_base,
            is_rate_limited=_is_rate_limit_error,
            is_quota_exhausted=_is_daily_quota_error,
            exhausted_block_seconds=_DAILY_QUOTA_BLOCK_SEC,
            label="Gemini",
            log=logger,
        )

    def _retry_transient(self, call, what: str):
        """Run ``call``; on a transient network error or 5xx wait and retry, up to len(transient_delays) times."""
        for retry in range(len(self.transient_delays) + 1):
            try:
                return call()
            except Exception as exc:
                if not is_transient_error(exc) or retry >= len(self.transient_delays):
                    raise
                delay = self.transient_delays[retry]
                self.transient_retries[type(exc).__name__] += 1
                logger.warning(
                    "%s call hit a transient %s (%s); retry %d/%d in %.0fs",
                    what, type(exc).__name__, str(exc)[:120], retry + 1, len(self.transient_delays), delay,
                )
                time.sleep(delay)

    def generate(
        self,
        stage: str,
        model: str,
        prompt: str,
        config: types.GenerateContentConfig | None = None,
    ) -> str:
        def _operation(api_key: str) -> str:
            response = self._client_for(api_key).models.generate_content(
                model=model, contents=prompt, config=config
            )

            usage = response.usage_metadata
            self.tracker.record(
                stage=stage,
                model=model,
                prompt_tokens=usage.prompt_token_count,
                output_tokens=usage.candidates_token_count,
            )
            return response.text

        return self._call_with_key_rotation(_operation, scope=model)

    def generate_vision(
        self,
        stage: str,
        model: str,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        *,
        max_retries: int | None = None,
        config: types.GenerateContentConfig | None = None,
    ) -> str:
        """Send one image + text prompt to a Gemini vision model."""

        def _operation(api_key: str) -> str:
            def attempt():
                client = self._vision_client_for(api_key)
                try:
                    return _call_with_deadline(
                        lambda: client.models.generate_content(
                            model=model,
                            contents=[
                                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                                prompt,
                            ],
                            config=config,
                        ),
                        VISION_CALL_TIMEOUT_SEC,
                    )
                except Exception as exc:
                    if isinstance(exc, httpx.TransportError):  # includes every timeout and the call deadline
                        self._drop_vision_client(api_key)
                    raise

            response = self._retry_transient(attempt, "vision")

            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                self.tracker.record(
                    stage=stage,
                    model=model,
                    prompt_tokens=usage.prompt_token_count or 0,
                    output_tokens=usage.candidates_token_count or 0,
                )
            return response.text or ""

        return self._call_with_key_rotation(
            _operation, scope=model, max_retries=max_retries
        )

    def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order."""
        if not texts:
            return []

        model_id = model if model.startswith("models/") else f"models/{model}"

        def _operation(api_key: str) -> list[list[float]]:
            embeddings = self._embedder_for(api_key, model_id)
            vectors: list[list[float]] = []
            for i, text in enumerate(texts):
                if i and _EMBED_REQUEST_INTERVAL_SEC > 0:
                    time.sleep(_EMBED_REQUEST_INTERVAL_SEC)
                vectors.extend(embeddings.embed_documents([text]))
            return vectors

        return self._call_with_key_rotation(_operation, scope=model_id)
