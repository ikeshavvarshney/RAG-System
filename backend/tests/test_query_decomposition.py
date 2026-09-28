import asyncio
import json

import pytest

from app.core.config import settings
from app.query.decomposition import decompose, looks_compound

COMPOUND = "How did revenue grow this quarter, and what does the appendix list about suppliers?"


def _reply(sub_questions) -> str:
    return json.dumps({"sub_questions": sub_questions})


@pytest.mark.parametrize(
    "question",
    [
        "Compare the 2024 and 2025 immigration projections in the outlook",
        "What was the funding request, and who approved it?",
        "What is the difference between the two scenarios in the report?",
        "What is the budget? Who signed off on it?",
    ],
)
def test_compound_signals_pass_the_gate(question):
    assert looks_compound(question)


@pytest.mark.parametrize(
    "question",
    [
        "What was the projected vacation access in 2012?",
        "revenue and growth",
        "How did revenue grow this quarter?",
    ],
)
def test_plain_or_short_questions_fail_the_gate(question):
    assert not looks_compound(question)


def test_gated_out_question_makes_no_llm_call(fake_llm):
    assert asyncio.run(decompose("How did revenue grow this quarter?")) == []
    assert fake_llm.calls == []


def test_returns_cleaned_sub_questions(fake_llm):
    fake_llm.replies["query_decomposition"] = _reply(["  How did revenue   grow? ", "What does the appendix list?"])

    assert asyncio.run(decompose(COMPOUND)) == ["How did revenue grow?", "What does the appendix list?"]


def test_accepts_a_bare_json_array(fake_llm):
    fake_llm.replies["query_decomposition"] = json.dumps(["First part?", "Second part?"])

    assert asyncio.run(decompose(COMPOUND)) == ["First part?", "Second part?"]


def test_prompt_frames_the_question_as_data(fake_llm):
    fake_llm.replies["query_decomposition"] = _reply([])

    asyncio.run(decompose(COMPOUND))

    prompt = fake_llm.calls[0][1]
    assert f"<question>\n{COMPOUND}\n</question>" in prompt and "never instructions" in prompt


def test_caps_at_the_configured_maximum(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "DECOMPOSITION_MAX_SUB_QUESTIONS", 3)
    fake_llm.replies["query_decomposition"] = _reply([f"Part {i}?" for i in range(6)])

    assert asyncio.run(decompose(COMPOUND)) == ["Part 0?", "Part 1?", "Part 2?"]


def test_drops_duplicates_and_echoes_of_the_original(fake_llm):
    fake_llm.replies["query_decomposition"] = _reply([COMPOUND, "Part A?", "part a?", "Part B?", "", 7])

    assert asyncio.run(decompose(COMPOUND)) == ["Part A?", "Part B?"]


@pytest.mark.parametrize("sub_questions", [[], ["Only one?"], [COMPOUND, "Only one?"], ["Same?", "same?"]])
def test_fewer_than_two_distinct_sub_questions_keeps_the_question_whole(fake_llm, sub_questions):
    fake_llm.replies["query_decomposition"] = _reply(sub_questions)

    assert asyncio.run(decompose(COMPOUND)) == []


@pytest.mark.parametrize("reply", ["not json", '{"sub_questions": "nope"}', "42", RuntimeError("down")])
def test_bad_or_failed_llm_output_keeps_the_question_whole(fake_llm, reply):
    fake_llm.replies["query_decomposition"] = reply

    assert asyncio.run(decompose(COMPOUND)) == []


def test_disabled_makes_no_llm_call(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "DECOMPOSITION_ENABLED", False)

    assert asyncio.run(decompose(COMPOUND)) == []
    assert fake_llm.calls == []
