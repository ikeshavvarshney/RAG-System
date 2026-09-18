import asyncio
import json

import pytest

from app.core.config import settings
from app.query.expansion import expand_query

QUESTION = "what drove revenue growth?"
VARIANTS = [f"variant {i}" for i in range(8)]


def _expand(question: str = QUESTION) -> list[str]:
    return asyncio.run(expand_query(question))


def test_original_is_first_followed_by_variants(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "EXPANSION_COUNT", 4)
    fake_llm.replies["query_expansion"] = json.dumps(VARIANTS[:3])

    assert _expand() == [QUESTION, *VARIANTS[:3]]


@pytest.mark.parametrize("count", [3, 4, 5])
def test_total_count_follows_config_even_if_model_returns_more(fake_llm, monkeypatch, count):
    monkeypatch.setattr(settings, "EXPANSION_COUNT", count)
    fake_llm.replies["query_expansion"] = json.dumps(VARIANTS)

    queries = _expand()

    assert len(queries) == count
    assert queries[0] == QUESTION
    assert f"{count - 1} alternative" in fake_llm.calls[0][1]


def test_fewer_variants_than_requested_is_accepted(fake_llm):
    fake_llm.replies["query_expansion"] = json.dumps(["only one"])

    assert _expand() == [QUESTION, "only one"]


def test_fenced_json_is_parsed(fake_llm):
    fake_llm.replies["query_expansion"] = '```json\n["alpha", "beta"]\n```'

    assert _expand() == [QUESTION, "alpha", "beta"]


def test_duplicates_and_non_strings_are_dropped(fake_llm):
    fake_llm.replies["query_expansion"] = json.dumps(
        [QUESTION.upper(), "alpha", "Alpha", 7, None, "  ", "beta"]
    )

    assert _expand() == [QUESTION, "alpha", "beta"]


@pytest.mark.parametrize(
    "reply",
    [
        RuntimeError("boom"),
        "I cannot help with that",
        "",
        "{}",
        '{"queries": ["a"]}',
        "[1, 2, 3]",
        '["unterminated',
        "null",
    ],
)
def test_malformed_output_degrades_to_original_query(fake_llm, reply):
    fake_llm.replies["query_expansion"] = reply

    assert _expand() == [QUESTION]


def test_question_with_braces_does_not_break_the_prompt(fake_llm):
    fake_llm.replies["query_expansion"] = '["alpha"]'

    assert _expand("what is {x} in {y}?") == ["what is {x} in {y}?", "alpha"]
