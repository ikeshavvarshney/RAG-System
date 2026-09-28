import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

class AllKeysBlocked(RuntimeError):
    pass


class KeyRotator:
    def __init__(self, raw_keys: str):
        self._keys=[k.strip() for k in raw_keys.split(",") if k.strip()]
        self._index=0
        self._lock=threading.Lock()
        self._blocked: dict[tuple[str, str], float] = {}

    def next(self, scope: str = "") -> str:
        if not self._keys:
            raise RuntimeError(
                "No API keys configured for this pool."
                "A Missing key must fail loudly here , not silently"
                "pass None into an API client"
            )
        with self._lock:
            now=time.monotonic()
            for _ in self._keys:
                key=self._keys[self._index % len(self._keys)]
                self._index+=1
                if self._blocked.get((key, scope), 0.0) <= now:
                    return key
            raise AllKeysBlocked(f"every key is out of quota for {scope or 'this pool'}")

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def block(self, key: str, scope: str, seconds: float) -> None:
        with self._lock:
            self._blocked[(key, scope)]=time.monotonic()+seconds

    def __len__(self) -> int:
        return len(self._keys)

def call_with_key_rotation(
    operation: Callable[[str], T],
    rotator: KeyRotator,
    *,
    scope: str = "",
    retries: int,
    backoff_base: float,
    is_rate_limited: Callable[[BaseException], bool],
    is_quota_exhausted: Callable[[BaseException], bool],
    exhausted_block_seconds: float,
    label: str,
    log: logging.Logger,
) -> T:
    """Run ``operation(api_key)``; on a rate-limit error rotate to the next key and retry with exponential backoff.

    An exhausted-quota error blocks that key for ``exhausted_block_seconds`` and moves on without spending a retry.
    """
    attempts = retries + 1
    last_exc: BaseException | None = None

    attempt = 0
    while attempt < attempts:
        api_key = rotator.next(scope)
        try:
            return operation(api_key)
        except Exception as exc:
            if not is_rate_limited(exc):
                raise
            last_exc = exc
            if is_quota_exhausted(exc):
                rotator.block(api_key, scope, exhausted_block_seconds)
                log.warning("%s quota exhausted for a key; skipping it", label)
                continue
            log.warning("%s rate-limited (attempt %d/%d); rotating key and retrying", label, attempt + 1, attempts)
            if attempt < attempts - 1 and backoff_base > 0:
                time.sleep(backoff_base * (2**attempt))
            attempt += 1

    assert last_exc is not None  # loop ran at least once
    raise last_exc


from app.core.config import settings

gemini_keys =KeyRotator(settings.GEMINI_API_KEYS)
tavily_keys=KeyRotator(settings.TAVILY_API_KEYS)
