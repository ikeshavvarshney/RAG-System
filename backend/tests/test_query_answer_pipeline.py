"""End-to-end behaviour of the answer stages wired into run_query: cache write-back, history, verdicts."""

import pytest

from app.query import cache, history, pipeline
from app.query.citations import NO_SUPPORT_ANSWER, ClaimVerdict, GroundednessResult, split_claims
from app.query.generation import NOT_IN_CONTEXT, GenerationResult
from app.query.guardrails.output import SAFE_FALLBACK_MESSAGE, SafetyVerdict
from app.query.history import FAILED_SUMMARY, NON_ANSWER_SUMMARY, SUMMARY_CHARS, summarize_answer
from app.query.verification import UNAVAILABLE, VerificationResult
from test_query_pipeline import (  # noqa: F401 - fixtures and helpers shared with the pipeline tests
    ANSWERING,
    RANKING,
    SAFE,
    SESSION,
    _run,
    corpus,
    session_uploads,
)

QUESTION = "how did revenue grow this quarter?"


@pytest.fixture(autouse=True)
def scripted(fake_llm):
    fake_llm.replies["query_guardrail"] = SAFE
    fake_llm.replies["query_expansion"] = "[]"
    return fake_llm


def _verdict(supported: bool, safety: str = "pass", reason: str = "ok"):
    async def verify(filter_result, passage_texts=None, *, stage=None):
        claims = [
            ClaimVerdict(index=i, text=t, markers=m, supported=supported, reason="r")
            for i, (t, m) in enumerate(split_claims(filter_result.answer))
        ]
        return VerificationResult(
            groundedness=GroundednessResult(claims=claims, score=float(supported)),
            safety=SafetyVerdict(verdict=safety, reason=reason, source="llm"),
        )

    return verify


def test_passing_answer_is_cached_once_and_a_repeat_hits_the_cache(corpus):
    first, _ = _run(QUESTION)

    assert first.terminated_at == "retrieved" and first.citations
    assert cache.get_answer_cache().count() == 1
    repeat, events = _run(QUESTION)
    assert repeat.terminated_at == "cache_hit"
    assert repeat.answer == first.answer and repeat.citations == first.citations
    assert cache.get_answer_cache().count() == 1
    assert ("generation", "started") not in events


def test_cache_entry_records_the_cited_source_docs(corpus):
    result, _ = _run(QUESTION)

    hit = cache.lookup(QUESTION, "persistent")

    assert hit is not None
    assert hit.cited_docs == sorted({c.source_doc for c in result.citations})
    assert hit.raw_question == QUESTION and hit.resolved_question == QUESTION
    assert hit.created_at > 0 and hit.corpus_scope == "persistent"


def test_cache_is_keyed_on_the_resolved_question(corpus, scripted):
    _run(QUESTION)
    scripted.replies["query_greeting"] = '{"kind": "other"}'
    scripted.replies["query_history"] = QUESTION

    again, _ = _run("and what about that?")

    assert again.terminated_at == "cache_hit"
    assert again.raw_question == "and what about that?" and again.resolved_question == QUESTION
    assert cache.get_answer_cache().count() == 1


def test_guardrail_rejected_answer_is_not_cached(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "verify_answer", _verdict(True, safety="fail", reason="unsafe content"))

    result, _ = _run(QUESTION)

    assert result.answer == SAFE_FALLBACK_MESSAGE and result.citations == []
    assert result.safety is not None and result.safety.verdict == "fail"
    assert cache.get_answer_cache().count() == 0
    assert history._store.turns(SESSION)[0].answer_summary == FAILED_SUMMARY


def test_verification_unavailable_answer_is_not_cached(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "verify_answer", _verdict(False, safety="fail", reason=UNAVAILABLE))

    result, _ = _run(QUESTION)

    assert result.answer == SAFE_FALLBACK_MESSAGE and result.citations == []
    assert cache.get_answer_cache().count() == 0


def test_non_answer_is_not_cached(corpus, monkeypatch):
    async def non_answer(question, passages=None, *, sub_contexts=None, stage=None):
        return GenerationResult(answer=NOT_IN_CONTEXT, is_non_answer=True)

    monkeypatch.setattr(pipeline, "generate_answer", non_answer)

    result, _ = _run(QUESTION)

    assert result.answer == NOT_IN_CONTEXT and result.citations == []
    assert cache.get_answer_cache().count() == 0
    assert history._store.turns(SESSION)[0].answer_summary == NON_ANSWER_SUMMARY


def test_all_unsupported_claims_give_the_honest_non_answer_end_to_end(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "verify_answer", _verdict(False))

    result, _ = _run(QUESTION)

    assert result.answer == NO_SUPPORT_ANSWER and result.citations == []
    assert result.groundedness is not None and result.groundedness.score == 0.0
    assert [c.reason for c in result.removed_claims] == ["unsupported"]
    assert cache.get_answer_cache().count() == 0
    assert history._store.turns(SESSION)[0].answer_summary == NON_ANSWER_SUMMARY


def test_session_scoped_answer_is_not_cached_or_served_to_the_corpus(session_uploads):
    own, _ = _run("how did revenue grow?")

    assert own.terminated_at == "retrieved" and own.citations
    assert cache.get_answer_cache().count() == 0
    other, _ = _run("how did revenue grow?", session_id="b" * 32)
    assert other.terminated_at == "retrieved"
    assert "u1" not in {c.chunk_id for c in other.context}


def test_result_carries_verdicts_and_final_answer(corpus):
    result, _ = _run(QUESTION)

    assert result.answer == "Stub answer [1]." and result.generation is not None
    assert result.groundedness is not None and result.groundedness.score == 1.0
    assert result.safety is not None and result.safety.verdict == "pass"
    assert len(result.citations) == 1 and result.removed_claims == []


def test_answer_stages_run_in_order_after_ranking(corpus):
    _, events = _run(QUESTION)

    assert events[-len(RANKING + ANSWERING) :] == RANKING + ANSWERING


def test_history_stores_a_short_marker_free_summary(corpus):
    _run(QUESTION)

    summary = history._store.turns(SESSION)[0].answer_summary

    assert summary == "Stub answer ." and "[" not in summary


def test_history_evicts_the_oldest_turn_first(corpus):
    questions = [f"how did revenue grow in region {name}?" for name in ("one", "two", "three", "four")]
    for question in questions:
        _run(question)

    turns = history._store.turns(SESSION)

    assert [t.raw_question for t in turns] == questions[-3:]


def test_summary_strips_markers_and_cuts_at_a_sentence_boundary():
    sentence = "Revenue rose eight percent in the year to four billion dollars [1][2]."
    answer = " ".join([sentence] * 6)

    summary = summarize_answer(answer)

    assert "[" not in summary and len(summary) <= SUMMARY_CHARS
    assert summary.endswith(".")
    assert summarize_answer("Short answer [1].") == "Short answer ."


def test_summary_falls_back_to_a_word_boundary():
    summary = summarize_answer("word " * 100)

    assert len(summary) <= SUMMARY_CHARS + 1 and summary.endswith("word…")


def _answering_from(*refs):
    """A generation result citing the given passages, one claim line each."""

    async def generate(question, passages=None, *, sub_contexts=None, stage=None):
        lines = [f"Claim number {r.number} is stated in this passage for the reader [{r.number}]." for r in refs]
        return GenerationResult(answer="\n".join(lines), passages={r.number: r for r in refs})

    return generate


def _corpus_ref(n):
    from app.query.generation import PassageRef

    return PassageRef(number=n, chunk_id=f"c{n}", kind="corpus", source_doc=f"doc{n}.pdf", page=1, score=0.9, snippet="s")


def _web_ref(n):
    from app.query.generation import PassageRef

    return PassageRef(
        number=n, chunk_id=f"web:{n}", kind="web", source_url=f"https://example.com/{n}", title="T", score=0.8, snippet="s"
    )


def test_corpus_only_answer_is_cached(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "generate_answer", _answering_from(_corpus_ref(1), _corpus_ref(2)))

    result, _ = _run(QUESTION)

    assert {c.kind for c in result.citations} == {"corpus"}
    assert cache.get_answer_cache().count() == 1


def test_mixed_corpus_and_web_answer_is_not_cached(corpus, monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "generate_answer", _answering_from(_corpus_ref(1), _web_ref(2)))

    with caplog.at_level("DEBUG", logger="app.query.pipeline"):
        result, _ = _run(QUESTION)

    assert {c.kind for c in result.citations} == {"corpus", "web"}
    assert cache.get_answer_cache().count() == 0
    assert "answer cites web sources" in caplog.text


def test_web_only_answer_is_not_cached(corpus, monkeypatch):
    monkeypatch.setattr(pipeline, "generate_answer", _answering_from(_web_ref(1), _web_ref(2)))

    result, _ = _run(QUESTION)

    assert {c.kind for c in result.citations} == {"web"}
    assert cache.get_answer_cache().count() == 0
