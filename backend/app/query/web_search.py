import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urldefrag, urlparse

import requests
from tavily import TavilyClient
from tavily.errors import ForbiddenError, UsageLimitExceededError
from tavily.errors import TimeoutError as TavilyTimeout

from app.core.config import settings
from app.core.key_rotation import AllKeysBlocked, call_with_key_rotation, tavily_keys
from app.core.usage import UsageTracker
from app.query.fusion import Candidate, Contribution

logger = logging.getLogger(__name__)

tracker = UsageTracker()

FailureKind = Literal["no_keys", "all_keys_blocked", "timeout", "quota", "http_error", "unexpected"]


@dataclass
class WebSearchResult:
    candidates: list[Candidate]
    status: Literal["ok", "failed"]
    failure: FailureKind | None = None


def _is_plan_limit(exc: BaseException) -> bool:
    # 432/433 reach us as ForbiddenError with no status code, only the message.
    return isinstance(exc, ForbiddenError) and "limit" in str(exc).lower()


def _is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, UsageLimitExceededError) or _is_plan_limit(exc)


def _classify(exc: Exception) -> FailureKind:
    if isinstance(exc, AllKeysBlocked):
        return "all_keys_blocked"
    if isinstance(exc, TavilyTimeout):
        return "timeout"
    if _is_rate_limited(exc):
        return "quota"
    if isinstance(exc, (ForbiddenError, requests.RequestException)) or type(exc).__module__.startswith("tavily"):
        return "http_error"
    return "unexpected"


def _search_once(api_key: str, query: str) -> dict[str, Any]:
    with TavilyClient(api_key=api_key) as client:
        response = client.search(
            query,
            max_results=settings.WEB_SEARCH_MAX_RESULTS,
            search_depth=settings.WEB_SEARCH_DEPTH,
            timeout=settings.WEB_SEARCH_TIMEOUT_SEC,
        )
    # The depth is in the label because Tavily bills per request by depth, not by tokens.
    tracker.record("web_search", f"tavily-{settings.WEB_SEARCH_DEPTH}", 0, 0)
    return response


def _search_sync(query: str) -> dict[str, Any]:
    return call_with_key_rotation(
        lambda api_key: _search_once(api_key, query),
        tavily_keys,
        scope="tavily",
        retries=settings.WEB_SEARCH_MAX_RETRIES,
        backoff_base=settings.WEB_SEARCH_BACKOFF_SEC,
        is_rate_limited=_is_rate_limited,
        is_quota_exhausted=_is_plan_limit,
        exhausted_block_seconds=settings.WEB_SEARCH_EXHAUSTED_BLOCK_SEC,
        label="Tavily",
        log=logger,
    )


def _clean_url(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _web_id(url: str) -> str:
    canonical = urldefrag(url)[0].rstrip("/")
    return f"web:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def normalize_results(response: dict[str, Any], query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()
    dropped = 0
    for rank, item in enumerate(response.get("results") or [], start=1):
        item = item if isinstance(item, dict) else {}
        url = _clean_url(item.get("url"))
        content = item.get("content")
        content = " ".join(content.split()) if isinstance(content, str) else ""
        if url is None or not content or _web_id(url) in seen:
            dropped += 1
            continue
        chunk_id = _web_id(url)
        seen.add(chunk_id)
        tavily_score = item.get("score")
        tavily_score = float(tavily_score) if isinstance(tavily_score, (int, float)) else None
        metadata: dict[str, Any] = {
            "source_type": "web",
            "source_url": url,
            "title": (item.get("title") or "").strip() or url,
        }
        if tavily_score is not None:
            metadata["tavily_score"] = tavily_score
        candidates.append(
            Candidate(
                chunk_id,
                content,
                metadata,
                tavily_score or 0.0,
                source="web",
                provenance=[Contribution("web", rank, query, tavily_score)],
            )
        )
    if dropped:
        logger.info("web search: dropped %d result(s) without a usable URL or content", dropped)
    return candidates


async def search_web(question: str) -> WebSearchResult:
    """Never raises: any failure is logged and returned as an empty result with a status."""
    if not len(tavily_keys):
        logger.warning("web search skipped: no Tavily keys configured")
        return WebSearchResult([], "failed", "no_keys")
    query = question[: settings.WEB_SEARCH_QUERY_MAX_CHARS]
    try:
        response = await asyncio.to_thread(_search_sync, query)
        candidates = normalize_results(response, query)
    except Exception as exc:  # noqa: BLE001 - web search is optional, the pipeline continues corpus-only
        kind = _classify(exc)
        logger.warning("web search failed (%s): %s: %s", kind, type(exc).__name__, exc)
        return WebSearchResult([], "failed", kind)
    logger.info("web search returned %d usable result(s)", len(candidates))
    return WebSearchResult(candidates, "ok")
