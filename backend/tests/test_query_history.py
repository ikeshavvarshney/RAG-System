import asyncio
import threading

import pytest

from app.query import history
from app.query.history import HistoryStore, record_turn, resolve_question

ACME = "What was Acme's Q3 revenue?"


def _resolve(session_id: str, question: str) -> tuple[str, str]:
    return asyncio.run(resolve_question(session_id, question))


def test_first_turn_returns_question_unchanged_without_llm_call(fake_llm):
    assert _resolve("s", ACME) == (ACME, ACME)
    assert fake_llm.calls == []


def test_follow_up_is_resolved_against_prior_turn(fake_llm):
    record_turn("s", ACME, ACME, "Revenue fell 4% to $1.2B.")
    fake_llm.replies["query_history"] = "Why did Acme's Q3 revenue decline?"

    raw, resolved = _resolve("s", "Why did it decline?")

    assert raw == "Why did it decline?"
    assert resolved == "Why did Acme's Q3 revenue decline?"
    prompt = fake_llm.calls[0][1]
    assert ACME in prompt and "Revenue fell 4%" in prompt and "Why did it decline?" in prompt


def test_prompt_forbids_rewriting_self_contained_or_unrelated_questions(fake_llm):
    record_turn("s", ACME, ACME)
    fake_llm.replies["query_history"] = "What does Landsat Next improve?"

    _resolve("s", "What does Landsat Next improve?")

    prompt = fake_llm.calls[0][1]
    assert "EXACTLY unchanged" in prompt
    assert "Never add context from an earlier topic" in prompt


def test_unrelated_question_after_several_turns_passes_through_unchanged(fake_llm):
    for i in range(4):
        record_turn("s", f"question {i}", f"question {i}", f"answer {i}")
    unrelated = "What does Landsat Next improve over Landsat 9?"
    fake_llm.replies["query_history"] = lambda prompt: unrelated

    assert _resolve("s", unrelated) == (unrelated, unrelated)


def test_reply_is_cleaned_of_quotes_and_padding(fake_llm):
    record_turn("s", ACME, ACME)
    fake_llm.replies["query_history"] = '  "Why did Acme revenue fall?"  '

    assert _resolve("s", "why?")[1] == "Why did Acme revenue fall?"


@pytest.mark.parametrize(
    "reply",
    [RuntimeError("boom"), "", "   ", "line one\nline two", "x" * 5000, None],
)
def test_rewrite_failure_or_bad_output_returns_the_raw_question(fake_llm, reply):
    record_turn("s", ACME, ACME)
    fake_llm.replies["query_history"] = reply if reply is not None else (lambda prompt: None)

    assert _resolve("s", "why did it fall?") == ("why did it fall?", "why did it fall?")


def test_window_holds_three_turns_and_evicts_oldest_first():
    store = HistoryStore(max_turns=3)
    for i in range(5):
        store.record_turn("s", f"raw {i}", f"resolved {i}")

    assert [t.raw_question for t in store.turns("s")] == ["raw 2", "raw 3", "raw 4"]


def test_default_window_is_three_turns():
    for i in range(6):
        record_turn("s", f"q{i}", f"q{i}")

    assert len(history._store.turns("s")) == 3


def test_stored_turns_hold_summaries_not_full_answers():
    record_turn("s", "q", "q", "word " * 2000)

    summary = history._store.turns("s")[0].answer_summary
    assert summary is not None and len(summary) <= 300


def test_answer_summary_defaults_to_none():
    record_turn("s", "q", "q")

    assert history._store.turns("s")[0].answer_summary is None


def test_sessions_are_isolated(fake_llm):
    record_turn("a", ACME, ACME)

    assert _resolve("b", "why did it fall?") == ("why did it fall?", "why did it fall?")
    assert fake_llm.calls == []


def test_total_sessions_are_capped_evicting_least_recently_used():
    store = HistoryStore(max_sessions=2)
    store.record_turn("a", "q", "q")
    store.record_turn("b", "q", "q")
    store.turns("a")
    store.record_turn("c", "q", "q")

    assert store.turns("b") == []
    assert store.turns("a") and store.turns("c")
    assert len(store) == 2


def test_idle_sessions_expire_after_the_idle_window():
    now = [0.0]
    store = HistoryStore(idle_seconds=24 * 3600, clock=lambda: now[0])
    store.record_turn("a", "q", "q")

    now[0] += 24 * 3600 - 1
    assert store.turns("a")
    now[0] += 24 * 3600 - 1
    assert store.turns("a")
    now[0] += 24 * 3600
    assert store.turns("a") == []
    assert len(store) == 0


def test_concurrent_recording_is_safe():
    store = HistoryStore(max_turns=3, max_sessions=50)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                store.record_turn(f"s{n % 10}", f"q{i}", f"q{i}")
                store.turns(f"s{n % 10}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(store) == 10
    assert all(len(store.turns(f"s{n}")) == 3 for n in range(10))
