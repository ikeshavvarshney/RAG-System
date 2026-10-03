import logging
from contextlib import contextmanager

import pytest

from app.query import generation
from app.query.citations import NO_SUPPORT_ANSWER
from app.query.generation import NOT_IN_CONTEXT
from app.query.guardrails.output import (
    LEAK_PHRASES,
    SAFE_FALLBACK_MESSAGE,
    SafetyVerdict,
    apply_output_guardrail,
    check_output_deterministic,
)

GOOD = "Revenue grew eight percent in the last fiscal year, driven mostly by the services segment [1]."
LOGGER = "app.query.guardrails.output"


def _failed(reason: str = "unsafe") -> SafetyVerdict:
    return SafetyVerdict(verdict="fail", reason=reason, source="llm")


def test_normal_cited_answer_passes():
    assert check_output_deterministic(GOOD).verdict == "pass"


@pytest.mark.parametrize("text", [NOT_IN_CONTEXT, NO_SUPPORT_ANSWER, NOT_IN_CONTEXT + " [1]"])
def test_honest_non_answers_pass(text):
    assert check_output_deterministic(text).verdict == "pass"


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_empty_and_whitespace_fail(text):
    assert check_output_deterministic(text).verdict == "fail"


@pytest.mark.parametrize("text", ["[1]", "[1][2]", " [1, 2] \n [3] "])
def test_only_markers_fails(text):
    assert "only citation markers" in check_output_deterministic(text).reason


def test_too_short_after_stripping_markers_fails():
    assert check_output_deterministic("Yes. [1]").verdict == "fail"


def test_repeated_fragment_fails():
    assert "repeated" in check_output_deterministic("The company is great. " * 6).reason


def test_repeated_fragment_with_differing_markers_fails():
    text = "The company is great [1]. The company is great [2]. The company is great [3]."
    assert check_output_deterministic(text).verdict == "fail"


def test_consecutive_word_loop_fails():
    assert check_output_deterministic("The total was " + "very " * 10 + "high").verdict == "fail"


def test_low_unique_word_ratio_on_long_text_fails():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 12
    assert "distinct" in check_output_deterministic(text).reason


@pytest.mark.parametrize(
    "text",
    [
        "Revenue grew eight percent last year and margins widened, while costs fell,",
        "Revenue grew eight percent last year according to the filing [1",
        "Revenue grew eight percent last year and was reported in (",
        "Revenue grew eight percent last year and costs fell [1,",
    ],
)
def test_truncated_answers_fail(text):
    assert check_output_deterministic(text).reason == "the answer looks truncated"


@pytest.mark.parametrize(
    "text",
    [GOOD, "Revenue grew eight percent last year. [1]", "- Revenue grew eight percent [1]\n- Costs fell [2]"],
)
def test_complete_answers_pass(text):
    assert check_output_deterministic(text).verdict == "pass"


@pytest.mark.parametrize("phrase", LEAK_PHRASES)
def test_leaked_prompt_phrases_fail(phrase):
    verdict = check_output_deterministic(f"Sure. {phrase.capitalize()}. Then more words here.")

    assert "leaks" in verdict.reason


def test_leaked_context_tag_fails():
    assert check_output_deterministic("Here is the answer: <context> some words follow </context>").verdict == "fail"


def test_leak_phrases_still_exist_in_the_real_prompt_templates():
    prompts = " ".join((generation._RULES, generation._SINGLE_PROMPT, generation._DECOMPOSED_PROMPT))
    collapsed = " ".join(prompts.split()).casefold()

    assert all(phrase in collapsed for phrase in LEAK_PHRASES)


def test_failure_returns_fallback_and_logs_the_reason(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = apply_output_guardrail("   ")

    assert result.answer == SAFE_FALLBACK_MESSAGE and not result.passed
    assert result.verdict.source == "deterministic"
    assert "the answer is empty" in caplog.text


def test_log_does_not_contain_the_answer(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        apply_output_guardrail("Revenue grew eight percent last year and margins widened,")

    assert "truncated" in caplog.text and "Revenue" not in caplog.text


def test_externally_failed_verdict_returns_fallback(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = apply_output_guardrail(GOOD, _failed("contains unsafe content"))

    assert result.answer == SAFE_FALLBACK_MESSAGE and not result.passed
    assert result.verdict.source == "llm" and "contains unsafe content" in caplog.text


def test_externally_passed_verdict_returns_the_original_answer():
    supplied = SafetyVerdict(verdict="pass", reason="ok", source="llm")

    result = apply_output_guardrail(GOOD, supplied)

    assert result.answer == GOOD and result.passed and result.verdict.source == "llm"


def test_deterministic_failure_wins_over_a_passing_external_verdict():
    supplied = SafetyVerdict(verdict="pass", reason="ok", source="llm")

    assert not apply_output_guardrail("", supplied).passed


def test_stage_event_is_emitted():
    events = []

    @contextmanager
    def stage(name):
        events.append(name)
        yield

    apply_output_guardrail(GOOD, stage=stage)

    assert events == ["output_guardrail"]
