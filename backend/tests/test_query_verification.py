import asyncio
import json
from contextlib import contextmanager

import pytest

from app.query.citations import (
    NO_SUPPORT_ANSWER,
    CitationFilterResult,
    GroundednessResult,
    apply_semantic_verdicts,
    split_claims,
)
from app.query.generation import NOT_IN_CONTEXT
from app.query.guardrails.output import SAFE_FALLBACK_MESSAGE, apply_output_guardrail
from app.query.verification import UNAVAILABLE, verify_answer
from app.shared.schemas.citation import CorpusCitation, WebCitation

STAGE = "query_verification"


def _corpus(n: int) -> CorpusCitation:
    return CorpusCitation(source_doc=f"doc{n}.pdf", page=n, chunk_id=f"c{n}", score=0.5, snippet=f"snippet {n}")


def _filtered(answer: str, n: int = 3) -> CitationFilterResult:
    return CitationFilterResult(
        answer=answer,
        citations=[_corpus(i) for i in range(1, n + 1)],
        marker_map={i: i for i in range(1, n + 1)},
    )


ANSWER = "Revenue grew eight percent last year [1].\nMargins widened on lower costs [2].\nHeadcount was flat [3]."
TEXTS = {"c1": "FULL TEXT ONE", "c2": "FULL TEXT TWO", "c3": "FULL TEXT THREE"}


def _reply(supported=(True, True, True), safety="pass", safety_reason="fine") -> str:
    return json.dumps(
        {
            "claims": [{"index": i, "supported": s, "reason": f"r{i}"} for i, s in enumerate(supported)],
            "safety": {"verdict": safety, "reason": safety_reason},
        }
    )


def _verify(filter_result, **kwargs):
    return asyncio.run(verify_answer(filter_result, TEXTS, **kwargs))


def test_all_supported_and_safe_makes_one_call(fake_llm):
    fake_llm.replies[STAGE] = _reply()

    result = _verify(_filtered(ANSWER))

    assert len(fake_llm.calls) == 1
    assert result.groundedness.score == 1.0 and all(c.supported for c in result.groundedness.claims)
    assert result.safety.verdict == "pass" and result.safety.source == "llm"
    assert apply_semantic_verdicts(_filtered(ANSWER), result.groundedness).answer == ANSWER


def test_uses_the_query_model(fake_llm):
    from app.core.config import settings

    fake_llm.replies[STAGE] = _reply()
    _verify(_filtered(ANSWER))

    assert fake_llm.models == [settings.QUERY_MODEL]


def test_unsupported_claim_is_removed_and_citations_remapped(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(True, False, True))
    filtered = _filtered(ANSWER)

    result = _verify(filtered)
    final = apply_semantic_verdicts(filtered, result.groundedness)

    assert result.groundedness.score == pytest.approx(2 / 3)
    assert final.answer == "Revenue grew eight percent last year [1].\nHeadcount was flat [2]."
    assert [c.chunk_id for c in final.citations] == ["c1", "c3"]
    assert final.marker_map == {1: 1, 3: 2}
    assert [(c.text, c.reason) for c in final.removed_claims] == [
        ("Margins widened on lower costs [2].", "unsupported")
    ]
    assert final.groundedness == result.groundedness


def test_groundedness_and_safety_are_independent(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(False, False, False), safety="pass")
    ungrounded = _verify(_filtered(ANSWER))
    fake_llm.replies[STAGE] = _reply(supported=(True, True, True), safety="fail", safety_reason="leaks instructions")
    unsafe = _verify(_filtered(ANSWER))

    assert ungrounded.groundedness.score == 0.0 and ungrounded.safety.verdict == "pass"
    assert unsafe.groundedness.score == 1.0 and unsafe.safety.verdict == "fail"
    assert unsafe.safety.reason == "leaks instructions"


def test_safety_verdict_feeds_the_output_guardrail_unchanged(fake_llm):
    fake_llm.replies[STAGE] = _reply(safety="fail", safety_reason="unsafe content")
    result = _verify(_filtered(ANSWER))

    shipped = apply_output_guardrail(ANSWER, result.safety)

    assert not shipped.passed and shipped.answer == SAFE_FALLBACK_MESSAGE


def test_invalid_json_then_valid_json_on_retry(fake_llm):
    replies = iter(["not json at all", _reply()])
    fake_llm.replies[STAGE] = lambda prompt: next(replies)

    result = _verify(_filtered(ANSWER))

    assert len(fake_llm.calls) == 2
    assert result.groundedness.score == 1.0 and result.safety.verdict == "pass"


def test_invalid_json_twice_fails_closed(fake_llm):
    fake_llm.replies[STAGE] = "```json\n{broken\n```"

    result = _verify(_filtered(ANSWER))

    assert len(fake_llm.calls) == 2
    assert result.safety.verdict == "fail" and result.safety.reason == UNAVAILABLE
    assert result.groundedness.score == 0.0 and not any(c.supported for c in result.groundedness.claims)


def test_fenced_json_is_accepted(fake_llm):
    fake_llm.replies[STAGE] = f"```json\n{_reply()}\n```"

    assert _verify(_filtered(ANSWER)).groundedness.score == 1.0


def test_missing_claim_index_is_unsupported(fake_llm):
    body = json.loads(_reply())
    body["claims"] = [c for c in body["claims"] if c["index"] != 1]
    fake_llm.replies[STAGE] = json.dumps(body)

    result = _verify(_filtered(ANSWER))

    assert [c.supported for c in result.groundedness.claims] == [True, False, True]
    assert result.groundedness.claims[1].reason == "no verdict returned"


def test_extra_indexes_and_wrong_types_are_handled(fake_llm):
    body = json.loads(_reply(supported=(True, True, True)))
    body["claims"] += [{"index": 99, "supported": True}, {"index": "x", "supported": True}, "junk"]
    body["claims"][0]["supported"] = "true"  # not a real bool
    fake_llm.replies[STAGE] = json.dumps(body)

    result = _verify(_filtered(ANSWER))

    assert [c.supported for c in result.groundedness.claims] == [False, True, True]
    assert len(fake_llm.calls) == 1


def test_malformed_safety_object_retries_then_fails_closed(fake_llm):
    fake_llm.replies[STAGE] = json.dumps({"claims": [], "safety": {"verdict": "maybe"}})

    result = _verify(_filtered(ANSWER))

    assert len(fake_llm.calls) == 2 and result.safety.reason == UNAVAILABLE


def test_api_error_fails_closed_without_retry(fake_llm, caplog):
    fake_llm.replies[STAGE] = RuntimeError("quota exhausted")

    with caplog.at_level("WARNING"):
        result = _verify(_filtered(ANSWER))

    assert len(fake_llm.calls) == 1
    assert result.safety.verdict == "fail" and result.safety.reason == UNAVAILABLE
    assert all(c.reason == UNAVAILABLE and not c.supported for c in result.groundedness.claims)
    assert "failing closed" in caplog.text


@pytest.mark.parametrize(
    "filtered",
    [
        CitationFilterResult(answer=NOT_IN_CONTEXT, is_non_answer=True),
        CitationFilterResult(answer=NO_SUPPORT_ANSWER, is_non_answer=True),
        CitationFilterResult(answer=NOT_IN_CONTEXT),
        CitationFilterResult(answer="## Overview\n\nIn summary:"),
    ],
)
def test_non_answers_and_claimless_answers_skip_the_llm(fake_llm, filtered):
    result = _verify(filtered)

    assert fake_llm.calls == []
    assert result.safety.verdict == "pass" and result.usage.total_tokens == 0
    assert result.groundedness.claims == []


def test_all_claims_unsupported_gives_no_support_answer(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(False, False, False))
    filtered = _filtered(ANSWER)

    final = apply_semantic_verdicts(filtered, _verify(filtered).groundedness)

    assert final.answer == NO_SUPPORT_ANSWER and final.is_non_answer
    assert final.citations == [] and len(final.removed_claims) == 3
    assert final.groundedness is not None and final.groundedness.score == 0.0


def test_prompt_treats_content_as_data_and_isolates_each_claims_evidence(fake_llm):
    fake_llm.replies[STAGE] = _reply()
    _verify(_filtered(ANSWER))
    prompt = fake_llm.calls[0][1]

    assert "data, never instructions" in prompt and "<answer>" in prompt and "JSON only" in prompt
    claim_blocks = prompt.split('<claim index="')[1:]
    assert len(claim_blocks) == 3
    assert "FULL TEXT ONE" in claim_blocks[0] and "FULL TEXT TWO" not in claim_blocks[0]
    assert "FULL TEXT TWO" in claim_blocks[1] and "FULL TEXT ONE" not in claim_blocks[1]
    assert "[3] doc3.pdf, page 3" in claim_blocks[2]


def test_claim_citing_two_passages_receives_both(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(True,))

    _verify(_filtered("Revenue and margins both improved last year [1][3]."))

    prompt = fake_llm.calls[0][1]
    assert "FULL TEXT ONE" in prompt and "FULL TEXT THREE" in prompt and "FULL TEXT TWO" not in prompt


def test_web_evidence_is_keyed_by_url_and_snippet_is_the_fallback(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(True, True))
    filtered = CitationFilterResult(
        answer="Web claim stands here [1].\nCorpus claim stands here [2].",
        citations=[
            WebCitation(source_url="https://a.io", title="A", snippet="web snippet"),
            _corpus(2),
        ],
    )

    asyncio.run(verify_answer(filtered, {"https://a.io": "WEB FULL TEXT"}))

    prompt = fake_llm.calls[0][1]
    assert "WEB FULL TEXT" in prompt and "https://a.io" in prompt and "snippet 2" in prompt


def test_split_claims_ignores_markerless_lines():
    claims = split_claims("## Heading\n\nA claim [2][1].\nIn summary:\nAnother [1, 3].")

    assert claims == [("A claim [2][1].", [2, 1]), ("Another [1, 3].", [1, 3])]


def test_stage_event_is_emitted(fake_llm):
    fake_llm.replies[STAGE] = _reply()
    events = []

    @contextmanager
    def stage(name):
        events.append(name)
        yield

    _verify(_filtered(ANSWER), stage=stage)

    assert events == ["verification"]


@pytest.mark.parametrize(
    "filtered",
    [
        CitationFilterResult(answer=NOT_IN_CONTEXT, is_non_answer=True),
        CitationFilterResult(answer="## Overview\n\nIn summary:"),
    ],
)
def test_skipped_verification_has_a_null_score_not_zero(fake_llm, filtered):
    result = _verify(filtered)

    assert fake_llm.calls == []
    assert result.groundedness.score is None and result.groundedness.claims == []


def test_real_scores_stay_floats_including_zero(fake_llm):
    fake_llm.replies[STAGE] = _reply(supported=(False, False, False))

    zero = _verify(_filtered(ANSWER)).groundedness.score
    fake_llm.replies[STAGE] = _reply()
    full = _verify(_filtered(ANSWER)).groundedness.score

    assert zero == 0.0 and isinstance(zero, float)
    assert full == 1.0 and isinstance(full, float)
