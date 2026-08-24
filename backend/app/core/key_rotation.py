import threading 

class KeyRotator:
    def __init__(self, raw_keys: str):
        self._keys=[k.strip() for k in raw_keys.split(",") if k.strip()]
        self._index=0
        self._lock=threading.Lock()

    def next(self) -> str:
        if not self._keys:
            raise RuntimeError(
                "No API keys configured for this pool."
                "A Missing key must fail loudly here , not silently"
                "pass None into an API client"
            )
        with self._lock:
            key=self._keys[self._index % len(self._keys)]
            self._index+=1
            return key

    def __len__(self) -> int:
        return len(self._keys)

from app.core.config import settings

gemini_keys =KeyRotator(settings.GEMINI_API_KEYS)
tavily_keys=KeyRotator(settings.TAVILY_API_KEYS)
