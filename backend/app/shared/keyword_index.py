"""In-memory BM25 keyword index over chunk text (INGEST-04, sparse half).

``rank_bm25`` has no persistence and no incremental delete. Per D-22 the index
is rebuilt in full from the vector store at startup and after any corpus
mutation — never maintained incrementally. Its only data source is the
:class:`VectorStore`: :meth:`KeywordIndex.rebuild` reads every chunk's text out
of Chroma (``VectorStore.all_chunks``) and holds the tokenised corpus plus a
position->chunk_id / position->corpus_scope map in memory.

Design choices
--------------
* One tokenizer. :func:`tokenize` is the single function both the build path
  and the query path call — never a copy — so the two can't silently diverge
  (a mismatch there is the classic silent BM25 bug).
* corpus_scope filtering is **post-scoring**. BM25 scores are computed once
  against the whole corpus, then the ranked list is filtered by scope and
  truncated to ``k``. Tradeoffs:
    - Term statistics (IDF, average document length) reflect the *entire*
      corpus, not the in-scope subset. A per-scope index would give "purer"
      statistics but means maintaining one index per scope and rebuilding all
      of them on any mutation; at our corpus size the difference is noise.
    - ``search`` may return fewer than ``k`` results: only in-scope chunks that
      actually contain a query term are returned; out-of-scope and non-matching
      chunks are never used to backfill.
* A "match" is defined by *token overlap*, not by score sign. A chunk is a
  candidate iff it shares at least one token with the query; the BM25 score
  only ranks those candidates. This matters on small corpora, where
  ``rank_bm25``'s Okapi variant floors (and can drive negative) the IDF of a
  term that appears in a large fraction of documents — a genuine keyword hit
  can score <= 0 there. Gating on overlap keeps "contains the keyword" as the
  hit criterion and lets an empty result mean "nothing matched".
"""

from __future__ import annotations

import string

from rank_bm25 import BM25Okapi

from app.shared.vector_store import VectorStore

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace.

    THE tokenizer. Both :meth:`KeywordIndex.rebuild` and
    :meth:`KeywordIndex.search` call this exact function object.
    """
    return text.lower().translate(_PUNCTUATION_TABLE).split()


class KeywordIndex:
    """A rebuildable in-memory BM25 index backed by a :class:`VectorStore`."""

    def __init__(self, vector_store: VectorStore):
        self._store = vector_store
        self._chunk_ids: list[str] = []
        self._scopes: list[str] = []
        self._token_sets: list[set[str]] = []
        self._bm25: BM25Okapi | None = None

    @classmethod
    def rebuild_from(cls, vector_store: VectorStore) -> "KeywordIndex":
        """Construct an index and populate it from ``vector_store`` immediately."""
        index = cls(vector_store)
        index.rebuild()
        return index

    def __len__(self) -> int:
        """Number of chunks currently in the index (0 before the first rebuild)."""
        return len(self._chunk_ids)

    def rebuild(self) -> None:
        """Discard the current index and rebuild it from every chunk in the store."""
        chunks = self._store.all_chunks()
        self._chunk_ids = [chunk.chunk_id for chunk in chunks]
        self._scopes = [chunk.corpus_scope for chunk in chunks]
        tokenized_corpus = [tokenize(chunk.text) for chunk in chunks]
        self._token_sets = [set(tokens) for tokens in tokenized_corpus]
        # BM25Okapi cannot be constructed on an empty corpus.
        self._bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None

    def search(
        self,
        query: str,
        k: int,
        corpus_scope: str | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``k`` ``(chunk_id, bm25_score)`` pairs, best score first.

        When ``corpus_scope`` is given, only chunks with that scope are returned
        (filtering happens after scoring — see module docstring).
        """
        if self._bm25 is None or not self._chunk_ids:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_set = set(query_tokens)

        scores = self._bm25.get_scores(query_tokens)

        ranked = sorted(
            zip(self._chunk_ids, self._scopes, self._token_sets, scores),
            key=lambda row: row[3],
            reverse=True,
        )

        results: list[tuple[str, float]] = []
        for chunk_id, scope, token_set, score in ranked:
            if query_set.isdisjoint(token_set):
                continue  # no shared token -> not a keyword hit
            if corpus_scope is not None and scope != corpus_scope:
                continue
            results.append((chunk_id, float(score)))
            if len(results) >= k:
                break
        return results
