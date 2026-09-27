import logging
import re
import threading
from dataclasses import dataclass, replace
from typing import Any

import tiktoken

from app.core.config import settings
from app.query.fusion import Candidate

logger = logging.getLogger(__name__)

_ENCODING = tiktoken.get_encoding("cl100k_base")
_WORD = re.compile(r"\w+")
_NEAR_DUPLICATE = 0.9

_model: Any = None
_model_lock = threading.Lock()


@dataclass
class RankedContext:
    passages: list[Candidate]
    reranked: bool
    token_count: int


def _load_model() -> Any:
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(
                settings.RERANK_MODEL, device="cpu", max_length=settings.RERANK_MAX_LENGTH
            )
    return _model


def warm_up() -> None:
    _load_model()


def _rerank(question: str, candidates: list[Candidate]) -> list[Candidate]:
    scores = _load_model().predict([(question, c.text) for c in candidates])
    rescored = [replace(c, score=float(s)) for c, s in zip(candidates, scores)]
    return sorted(rescored, key=lambda c: c.score, reverse=True)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def _is_near_duplicate(words: set[str], kept: list[set[str]]) -> bool:
    for other in kept:
        union = words | other
        if union and len(words & other) / len(union) >= _NEAR_DUPLICATE:
            return True
    return False


def consolidate(candidates: list[Candidate], top_k: int, token_budget: int) -> tuple[list[Candidate], int]:
    passages: list[Candidate] = []
    kept_words: list[set[str]] = []
    used = 0
    for candidate in candidates:
        if len(passages) == top_k:
            break
        words = _words(candidate.text)
        if _is_near_duplicate(words, kept_words):
            continue
        tokens = len(_ENCODING.encode(candidate.text))
        if used + tokens > token_budget:
            continue
        passages.append(candidate)
        kept_words.append(words)
        used += tokens
    return passages, used


def rerank_and_consolidate(question: str, candidates: list[Candidate]) -> RankedContext:
    """Rerank against the user's question, not the expansions, then fit the budget."""
    shortlist = candidates[: settings.RERANK_CANDIDATES]
    reranked = False
    if shortlist:
        try:
            shortlist = _rerank(question, shortlist)
            reranked = True
        except Exception:  # noqa: BLE001 - fused order is still a usable ranking
            logger.warning("reranking failed; keeping fused order", exc_info=True)
    passages, used = consolidate(shortlist, settings.RERANK_TOP_K, settings.CONTEXT_TOKEN_BUDGET)
    return RankedContext(passages, reranked, used)
