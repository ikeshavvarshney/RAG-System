"""Persistent Chroma vector store for chunk embeddings (INGEST-04).

A single collection (``chunks``) holds every dense vector regardless of
``corpus_scope``; the scope is stored as chunk metadata so the Week 4 query
pipeline (USERDOC-01) can filter by it — and by any other metadata field — at
query time within the one collection.

Design choices
--------------
* ``dense_vector_id`` == ``chunk_id``. Chroma is keyed by ``chunk_id`` (the
  ``ids`` passed to ``upsert``), so the vector's identity in the store already
  *is* the chunk id. A separate id would be pure indirection: every retrieval
  path already carries the chunk id, and there is nothing to look up in
  between. Setting ``dense_vector_id = chunk_id`` also lets
  ``chunk.dense_vector_id is not None`` serve as a reliable "this chunk has been
  written to the vector store" flag.
* Metadata ``None`` handling: Chroma only accepts ``str | int | float | bool``
  metadata values. ``page`` (``Optional[int]``) and ``location``
  (``Optional[str]``) are therefore **omitted entirely** when ``None`` rather
  than stored as a sentinel. A sentinel (``-1``, ``""``) would leak into
  ``where`` filters and misrepresent "unknown" as a real value; an absent key
  simply never matches a filter on that field, and is restored as ``None`` when
  a :class:`Chunk` is reconstructed on read.
* Retrieving full chunk data: the chunk ``text`` is stored as Chroma's
  ``documents`` payload, so :meth:`search` returns :class:`SearchResult` objects
  that carry the fully reconstructed :class:`Chunk` (text + metadata), not just
  ids and distances. :meth:`get` does the same for a list of ids. Week 4 gets
  chunk text straight off the search results — no second round trip.
"""

from __future__ import annotations

from dataclasses import dataclass

import chromadb

from app.core.config import settings
from app.shared.schemas.chunk import Chunk

COLLECTION_NAME = "chunks"

# Fields that are always present on a Chunk (non-Optional str) and so are
# always written to Chroma metadata.
_REQUIRED_META_FIELDS = ("source_doc", "chunk_type", "extraction_method", "corpus_scope")


@dataclass
class SearchResult:
    """One hit from :meth:`VectorStore.search`.

    ``distance`` is the cosine distance reported by Chroma (lower = more
    similar; 0.0 is identical). ``chunk`` is the fully reconstructed chunk,
    including its text.
    """

    chunk_id: str
    distance: float
    chunk: Chunk


class VectorStore:
    """Thin wrapper over a persistent Chroma collection of chunk embeddings."""

    def __init__(self, path: str | None = None):
        self._path = path or settings.CHROMA_PATH
        self._client = chromadb.PersistentClient(path=self._path)
        # embedding_function=None: we always supply our own vectors, so Chroma
        # must never try to download / run its default embedder.
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert(self, pairs: list[tuple[Chunk, list[float]]]) -> list[Chunk]:
        """Write/update ``(chunk, vector)`` pairs, keyed by ``chunk_id``.

        Idempotent: re-upserting a ``chunk_id`` overwrites its vector, metadata
        and text instead of adding a duplicate. Returns each chunk with
        ``dense_vector_id`` populated (== ``chunk_id``), in input order.
        """
        if not pairs:
            return []

        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict] = []
        documents: list[str] = []
        stored_chunks: list[Chunk] = []

        for chunk, vector in pairs:
            stored = chunk.model_copy(update={"dense_vector_id": chunk.chunk_id})
            ids.append(stored.chunk_id)
            embeddings.append([float(x) for x in vector])
            metadatas.append(_to_metadata(stored))
            documents.append(stored.text)
            stored_chunks.append(stored)

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )
        return stored_chunks

    def delete_by_document(self, source_doc: str, corpus_scope: str) -> list[str]:
        """Delete every chunk matching both ``source_doc`` and ``corpus_scope``.

        Returns the deleted ``chunk_id``s (empty list if nothing matched).
        """
        where = {
            "$and": [
                {"source_doc": {"$eq": source_doc}},
                {"corpus_scope": {"$eq": corpus_scope}},
            ]
        }
        matched = self._collection.get(where=where)
        ids = list(matched["ids"])
        if ids:
            self._collection.delete(ids=ids)
        return ids

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def search(
        self,
        query_vector: list[float],
        k: int,
        where: dict | None = None,
    ) -> list[SearchResult]:
        """Nearest-neighbour search, optionally filtered by chunk metadata.

        ``where`` is passed straight through to Chroma, e.g.
        ``{"corpus_scope": "persistent"}`` or ``{"extraction_method": "vision"}``.
        """
        result = self._collection.query(
            query_embeddings=[[float(x) for x in query_vector]],
            n_results=k,
            where=where or None,
        )

        ids = result["ids"][0]
        distances = result["distances"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]

        return [
            SearchResult(
                chunk_id=cid,
                distance=float(dist),
                chunk=_to_chunk(cid, doc, meta),
            )
            for cid, dist, doc, meta in zip(ids, distances, documents, metadatas)
        ]

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        """Fetch fully reconstructed chunks by id (missing ids are skipped)."""
        if not chunk_ids:
            return []
        result = self._collection.get(ids=chunk_ids)
        return [
            _to_chunk(cid, doc, meta)
            for cid, doc, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            )
        ]

    def all_chunks(self) -> list[Chunk]:
        """Return every stored chunk, fully reconstructed.

        Used by the BM25 keyword index, which has no persistence of its own and
        rebuilds from scratch off the vector store (D-22).
        """
        result = self._collection.get()
        return [
            _to_chunk(cid, doc, meta)
            for cid, doc, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            )
        ]

    def count(self) -> int:
        """Total number of vectors currently stored."""
        return self._collection.count()


# --------------------------------------------------------------------------- #
# Chunk <-> Chroma record mapping
# --------------------------------------------------------------------------- #
def _to_metadata(chunk: Chunk) -> dict:
    meta: dict = {field: getattr(chunk, field) for field in _REQUIRED_META_FIELDS}
    # Optional fields: omit when None (see module docstring).
    if chunk.page is not None:
        meta["page"] = chunk.page
    if chunk.location is not None:
        meta["location"] = chunk.location
    if chunk.embedding_model is not None:
        meta["embedding_model"] = chunk.embedding_model
    return meta


def _to_chunk(chunk_id: str, document: str, metadata: dict) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=document,
        source_doc=metadata["source_doc"],
        page=metadata.get("page"),
        location=metadata.get("location"),
        chunk_type=metadata["chunk_type"],
        extraction_method=metadata["extraction_method"],
        corpus_scope=metadata["corpus_scope"],
        dense_vector_id=chunk_id,
        embedding_model=metadata.get("embedding_model"),
    )
