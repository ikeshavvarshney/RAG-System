import asyncio
import logging

import pytest
import requests
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeout

from app.core.config import settings
from app.core.key_rotation import KeyRotator
from app.core.usage import UsageTracker
from app.query import web_search
from app.query.web_search import normalize_results, search_web


def _item(url="https://example.com/a", content="Some snippet.", title="Example", score=0.9):
    return {"url": url, "content": content, "title": title, "score": score}


class FakeTavily:
    """Scripted Tavily client: per api key, a list of responses or exceptions consumed in order."""

    script: dict[str, list] = {}
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, api_key=None):
        self.api_key = api_key

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query, **kwargs):
        FakeTavily.calls.append((self.api_key, query, kwargs))
        reply = FakeTavily.script[self.api_key].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def tavily(monkeypatch):
    FakeTavily.script, FakeTavily.calls = {}, []
    monkeypatch.setattr(web_search, "TavilyClient", FakeTavily)
    monkeypatch.setattr(web_search, "tavily_keys", KeyRotator("k1,k2"))
    monkeypatch.setattr(web_search, "tracker", UsageTracker())
    monkeypatch.setattr(settings, "WEB_SEARCH_BACKOFF_SEC", 0.0)
    return FakeTavily


def _search(question="what is new?"):
    return asyncio.run(search_web(question))


def test_results_are_normalised_into_web_candidates(tavily):
    tavily.script["k1"] = [{"results": [_item(), _item("https://example.org/b", "Other.", "Other", 0.4)]}]

    result = _search("q")

    assert result.status == "ok" and result.failure is None
    first, second = result.candidates
    assert first.source == "web"
    assert first.text == "Some snippet."
    assert first.metadata == {
        "source_type": "web",
        "source_url": "https://example.com/a",
        "title": "Example",
        "tavily_score": 0.9,
    }
    assert first.score == 0.9
    assert [(p.retriever, p.rank, p.query, p.score) for p in first.provenance] == [("web", 1, "q", 0.9)]
    assert second.provenance[0].rank == 2


def test_every_result_keeps_its_source_url(tavily):
    tavily.script["k1"] = [{"results": [_item(f"https://example.com/{i}") for i in range(5)]}]

    result = _search()

    assert len(result.candidates) == 5
    assert all(c.metadata["source_url"].startswith("https://example.com/") for c in result.candidates)


@pytest.mark.parametrize(
    "bad",
    [
        {"content": "no url key", "title": "t"},
        {"url": "", "content": "blank"},
        {"url": "   ", "content": "spaces"},
        {"url": None, "content": "none"},
        {"url": 42, "content": "not a string"},
        {"url": "javascript:alert(1)", "content": "bad scheme"},
        {"url": "example.com/no-scheme", "content": "no scheme"},
        "not a dict",
    ],
)
def test_results_without_a_usable_url_are_dropped(bad):
    kept = _item("https://example.com/keep")

    candidates = normalize_results({"results": [bad, kept]}, "q")

    assert [c.metadata["source_url"] for c in candidates] == ["https://example.com/keep"]


def test_results_without_content_and_duplicate_urls_are_dropped():
    response = {
        "results": [
            _item("https://example.com/a"),
            _item("https://example.com/a#section", "Same page, another fragment."),
            _item("https://example.com/empty", content="  "),
        ]
    }

    assert [c.metadata["source_url"] for c in normalize_results(response, "q")] == ["https://example.com/a"]


def test_id_is_stable_and_derived_from_the_url():
    a = normalize_results({"results": [_item("https://example.com/a")]}, "q1")[0]
    again = normalize_results({"results": [_item("https://example.com/a/", "Changed text.")]}, "q2")[0]
    other = normalize_results({"results": [_item("https://example.com/b")]}, "q1")[0]

    assert a.chunk_id == again.chunk_id
    assert a.chunk_id != other.chunk_id
    assert a.chunk_id.startswith("web:")


def test_missing_title_and_score_are_handled():
    candidate = normalize_results({"results": [{"url": "https://example.com/x", "content": "c"}]}, "q")[0]

    assert candidate.metadata["title"] == "https://example.com/x"
    assert "tavily_score" not in candidate.metadata
    assert candidate.score == 0.0 and candidate.provenance[0].score is None


def test_config_drives_the_request(tavily, monkeypatch):
    monkeypatch.setattr(settings, "WEB_SEARCH_MAX_RESULTS", 3)
    monkeypatch.setattr(settings, "WEB_SEARCH_TIMEOUT_SEC", 4.5)
    monkeypatch.setattr(settings, "WEB_SEARCH_DEPTH", "advanced")
    monkeypatch.setattr(settings, "WEB_SEARCH_QUERY_MAX_CHARS", 30)
    tavily.script["k1"] = [{"results": []}]

    result = _search("x" * 100)

    assert result.status == "ok" and result.candidates == []
    _, query, kwargs = tavily.calls[0]
    assert query == "x" * 30
    assert kwargs == {"max_results": 3, "search_depth": "advanced", "timeout": 4.5}


def test_rate_limit_rotates_to_the_next_key(tavily):
    tavily.script["k1"] = [UsageLimitExceededError("too many requests")]
    tavily.script["k2"] = [{"results": [_item()]}]

    result = _search()

    assert result.status == "ok" and len(result.candidates) == 1
    assert [key for key, _, _ in tavily.calls] == ["k1", "k2"]


def test_plan_limit_blocks_the_key_and_rotates(tavily):
    tavily.script["k1"] = [ForbiddenError("This request exceeds your plan's set usage limit.")]
    tavily.script["k2"] = [{"results": [_item()]}, {"results": [_item()]}]

    first = _search()
    second = _search()

    assert first.status == second.status == "ok"
    assert [key for key, _, _ in tavily.calls] == ["k1", "k2", "k2"]


def test_all_keys_over_their_plan_limit_fails_fast_once_blocked(tavily):
    tavily.script["k1"] = [ForbiddenError("plan usage limit exceeded")]
    tavily.script["k2"] = [ForbiddenError("plan usage limit exceeded")]

    first = _search()
    second = _search()

    assert (first.status, first.failure, first.candidates) == ("failed", "all_keys_blocked", [])
    assert (second.status, second.failure) == ("failed", "all_keys_blocked")
    assert len(tavily.calls) == 2


def test_persistent_rate_limit_fails_as_quota_after_retries(tavily, monkeypatch):
    monkeypatch.setattr(settings, "WEB_SEARCH_MAX_RETRIES", 2)
    tavily.script["k1"] = [UsageLimitExceededError("429")] * 3
    tavily.script["k2"] = [UsageLimitExceededError("429")] * 3

    result = _search()

    assert (result.status, result.failure, result.candidates) == ("failed", "quota", [])
    assert len(tavily.calls) == 3


@pytest.mark.parametrize(
    "error, kind",
    [
        (TavilyTimeout(10), "timeout"),
        (BadRequestError("query too long"), "http_error"),
        (InvalidAPIKeyError("bad key"), "http_error"),
        (ForbiddenError("forbidden"), "http_error"),
        (requests.exceptions.ConnectionError("dns"), "http_error"),
        (RuntimeError("boom"), "unexpected"),
    ],
)
def test_failures_return_an_empty_list_and_never_raise(tavily, error, kind):
    tavily.script["k1"] = [error]

    result = _search()

    assert (result.candidates, result.status, result.failure) == ([], "failed", kind)


def test_no_keys_fails_without_calling_the_client(tavily, monkeypatch, caplog):
    monkeypatch.setattr(web_search, "tavily_keys", KeyRotator(""))

    with caplog.at_level(logging.WARNING, logger="app.query.web_search"):
        result = _search()

    assert (result.candidates, result.status, result.failure) == ([], "failed", "no_keys")
    assert tavily.calls == []
    assert "no Tavily keys" in caplog.text


def test_malformed_response_is_a_failure_not_an_exception(tavily):
    tavily.script["k1"] = [["not", "a", "dict"]]

    result = _search()

    assert (result.candidates, result.status, result.failure) == ([], "failed", "unexpected")


def test_failure_is_logged(tavily, caplog):
    tavily.script["k1"] = [TavilyTimeout(10)]

    with caplog.at_level(logging.WARNING, logger="app.query.web_search"):
        _search()

    assert "web search failed (timeout)" in caplog.text


def test_each_request_is_counted_in_usage(tavily):
    tavily.script["k1"] = [UsageLimitExceededError("429")]
    tavily.script["k2"] = [{"results": [_item()]}]

    _search()

    assert web_search.tracker.request_count("web_search") == 1
    assert web_search.tracker.total_tokens() == 0


def test_usage_label_carries_the_search_depth(tavily, monkeypatch):
    monkeypatch.setattr(settings, "WEB_SEARCH_DEPTH", "advanced")
    tavily.script["k1"] = [{"results": [_item()]}]

    _search()

    assert [entry.model for entry in web_search.tracker._entries] == ["tavily-advanced"]
