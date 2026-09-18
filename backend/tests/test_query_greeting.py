import asyncio

import pytest

from app.query.greeting import classify_greeting, match_greeting


def _detect(text: str) -> str | None:
    return match_greeting(text) or asyncio.run(classify_greeting(text))


@pytest.mark.parametrize("text", ["hi", "hello there", "thanks!", "Hello!", "  Thank you. ", "bye"])
def test_obvious_small_talk_short_circuits_without_api_call(fake_llm, text):
    assert _detect(text)
    assert fake_llm.calls == []


def test_question_containing_greeting_words_is_not_short_circuited(fake_llm):
    assert _detect("what does the report say about hello world?") is None
    assert fake_llm.calls == []


def test_short_unrecognised_input_is_classified_by_llm(fake_llm):
    fake_llm.replies["query_greeting"] = '{"kind": "greeting"}'

    assert _detect("good evening to you") is not None
    assert [stage for stage, _ in fake_llm.calls] == ["query_greeting"]


def test_llm_saying_other_passes_through(fake_llm):
    fake_llm.replies["query_greeting"] = '{"kind": "other"}'

    assert _detect("revenue in 2023?") is None


@pytest.mark.parametrize(
    "reply",
    [RuntimeError("boom"), "gibberish", '{"kind": "poem"}', '{"kind": 3}', '{"kind": ["greeting"]}', "[]", '{"other": 1}'],
)
def test_unsure_llm_passes_through(fake_llm, reply):
    fake_llm.replies["query_greeting"] = reply

    assert _detect("revenue in 2023?") is None


def test_classifier_only_sees_inputs_of_four_words_or_fewer(fake_llm):
    fake_llm.replies["query_greeting"] = '{"kind": "other"}'

    assert _detect("one two three four") is None
    assert len(fake_llm.calls) == 1

    assert _detect("one two three four five") is None
    assert len(fake_llm.calls) == 1
