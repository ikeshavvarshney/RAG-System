import threading
import time

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

    def block(self, key: str, scope: str, seconds: float) -> None:
        with self._lock:
            self._blocked[(key, scope)]=time.monotonic()+seconds

    def __len__(self) -> int:
        return len(self._keys)

from app.core.config import settings

gemini_keys =KeyRotator(settings.GEMINI_API_KEYS)
tavily_keys=KeyRotator(settings.TAVILY_API_KEYS)
