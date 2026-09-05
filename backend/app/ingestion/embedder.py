"""Dense embedding of chunks and query strings (INGEST-04, dense half).

``embed_chunks`` turns a list of :class:`Chunk` into ``(chunk, vector)`` pairs;
``embed_query`` embeds a single arbitrary string for the Week 4 query pipeline.
Both go through :class:`GeminiClient` (key rotation + rate-limit retry) and both
are backed by the same on-disk content-hash cache, so a text that has been
embedded once — whether as a chunk or as a query — never hits the API again.

Design notes
------------
* Vectors are returned *alongside* chunks as ``list[tuple[Chunk, list[float]]]``
  rather than stored on the model: ``Chunk`` has no vector field (by design —
  a 768-float list has no business travelling through the API layer), and
  ``dense_vector_id`` is assigned later by ``vector_store.py`` when the vector
  is actually written to Chroma. ``embedding_model`` *is* a ``Chunk`` field and
  is set here (to ``settings.EMBEDDING_MODEL``) on a copy of each input chunk.
* Cache format: one JSON file per entry, named ``<sha256>.json`` under
  ``settings.EMBEDDING_CACHE_DIR``, containing just the vector as a JSON array.
  Chosen over sqlite/shelve because entries are write-once/read-many, trivially
  inspectable, corruption is isolated to a single key, and concurrent writers
  never contend on one file. The hash key is ``sha256(model_id \\x00 text)``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.core.config import settings
from app.core.gemini_client import GeminiClient
from app.shared.schemas.chunk import Chunk

logger = logging.getLogger(__name__)

# Texts per outbound embed_batch call. text-embedding-004 accepts up to 100
# instances per request; 64 leaves headroom and keeps payloads modest.
EMBED_BATCH_SIZE = 64

_client = GeminiClient()


def embed_chunks(chunks: list[Chunk]) -> list[tuple[Chunk, list[float]]]:
    """Embed ``chunks`` and return ``(chunk, vector)`` pairs in input order.

    Each returned chunk is a copy with ``embedding_model`` set to
    ``settings.EMBEDDING_MODEL``. ``dense_vector_id`` is left untouched — that
    is assigned downstream when the vector is written to the vector store.
    """
    if not chunks:
        return []

    model_id = _model_id()
    vectors = _embed_texts([c.text for c in chunks], model_id)

    out: list[tuple[Chunk, list[float]]] = []
    for chunk, vector in zip(chunks, vectors):
        embedded = chunk.model_copy(update={"embedding_model": settings.EMBEDDING_MODEL})
        out.append((embedded, vector))
    return out


def embed_query(text: str) -> list[float]:
    """Embed a single query string, sharing the chunk-embedding cache."""
    return _embed_texts([text], _model_id())[0]


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _model_id() -> str:
    """Canonical model id used for both the API call and the cache key."""
    model = settings.EMBEDDING_MODEL
    return model if model.startswith("models/") else f"models/{model}"


def _embed_texts(texts: list[str], model_id: str) -> list[list[float]]:
    """Return a vector per text, serving cache hits and batching cache misses.

    Identical strings within the input are collapsed so each unique cache miss
    is sent to the API exactly once.
    """
    results: list[list[float] | None] = [None] * len(texts)

    misses_by_text: dict[str, list[int]] = {}
    for i, text in enumerate(texts):
        cached = _cache_get(_cache_key(text, model_id))
        if cached is not None:
            results[i] = cached
        else:
            misses_by_text.setdefault(text, []).append(i)

    unique_texts = list(misses_by_text)
    for start in range(0, len(unique_texts), EMBED_BATCH_SIZE):
        batch = unique_texts[start : start + EMBED_BATCH_SIZE]
        vectors = _client.embed_batch(batch, model=model_id)
        for text, vector in zip(batch, vectors):
            _cache_put(_cache_key(text, model_id), vector)
            for i in misses_by_text[text]:
                results[i] = vector

    return [v if v is not None else [] for v in results]


def _cache_key(text: str, model_id: str) -> str:
    return hashlib.sha256(f"{model_id}\x00{text}".encode("utf-8")).hexdigest()


def _cache_dir() -> Path:
    path = Path(settings.EMBEDDING_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_get(key: str) -> list[float] | None:
    path = _cache_dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        logger.warning("Ignoring unreadable embedding cache entry %s", path.name)
        return None


def _cache_put(key: str, vector: list[float]) -> None:
    directory = _cache_dir()
    path = directory / f"{key}.json"
    tmp = directory / f"{key}.json.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(vector, fh)
        tmp.replace(path)  # atomic on the same filesystem
    except OSError:
        logger.warning("Could not persist embedding cache entry %s", path.name)
