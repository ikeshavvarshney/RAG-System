import asyncio
import json
import logging

import pytest

from app.core.config import settings
from app.query.fusion import Candidate, Contribution
from app.query.sufficiency import assess, compute_scores

STAGE = "query_sufficiency"


@pytest.fixture(autouse=True)
def thresholds(monkeypatch):
    monkeypatch.setattr(settings, "SUFFICIENCY_HIGH_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "SUFFICIENCY_LOW_THRESHOLD", 0.7)
    monkeypatch.setattr(settings, "SUFFICIENCY_LLM_ENABLED", True)


def _candidate(chunk_id: str, dense: float | None = None, keyword: float | None = None, text: str = "") -> Candidate:
    provenance = []
    if dense is not None:
        provenance.append(Contribution("vector", 1, "q", dense))
    if keyword is not None:
        provenance.append(Contribution("keyword", 1, None, keyword))
    return Candidate(chunk_id, text or f"text {chunk_id}", {"source_doc": f"{chunk_id}.pdf"}, 0.03, provenance=provenance)


def _assess(candidates, question="what is asked?"):
    return asyncio.run(assess(question, candidates))


def test_high_score_is_sufficient_without_an_llm_call(fake_llm):
    result = _assess([_candidate("a", dense=0.93)])

    assert result.sufficient and result.method == "score"
    assert fake_llm.calls == []


def test_low_score_is_insufficient_without_an_llm_call(fake_llm):
    result = _assess([_candidate("a", dense=0.55)])

    assert not result.sufficient and result.method == "score"
    assert fake_llm.calls == []


def test_thresholds_are_inclusive_at_high_and_exclusive_at_low(fake_llm):
    fake_llm.replies[STAGE] = json.dumps({"sufficient": False, "reason": "no"})

    assert _assess([_candidate("a", dense=0.9)]).method == "score"
    assert _assess([_candidate("a", dense=0.7)]).method == "llm"


def test_no_candidates_is_insufficient_without_an_llm_call(fake_llm):
    result = _assess([])

    assert not result.sufficient and result.method == "score"
    assert fake_llm.calls == []


@pytest.mark.parametrize("verdict", [True, False])
def test_grey_zone_defers_to_the_llm(fake_llm, verdict):
    fake_llm.replies[STAGE] = json.dumps({"sufficient": verdict, "reason": "because"})

    result = _assess([_candidate("a", dense=0.8)])

    assert (result.sufficient, result.reason, result.method) == (verdict, "because", "llm")
    assert [stage for stage, _ in fake_llm.calls] == [STAGE]
    assert fake_llm.models == [settings.SUFFICIENCY_LLM_MODEL]


def test_fenced_json_verdict_is_parsed(fake_llm):
    fake_llm.replies[STAGE] = '```json\n{"sufficient": true, "reason": "covered"}\n```'

    result = _assess([_candidate("a", dense=0.8)])

    assert result.sufficient and result.method == "llm"


def test_llm_sees_only_top_k_passages_truncated(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "SUFFICIENCY_LLM_TOP_K", 2)
    monkeypatch.setattr(settings, "SUFFICIENCY_LLM_PASSAGE_CHARS", 60)
    fake_llm.replies[STAGE] = json.dumps({"sufficient": True, "reason": "ok"})
    candidates = [
        _candidate("a", dense=0.8, text="alpha " * 100),
        _candidate("b", dense=0.75, text="beta " * 100),
        _candidate("c", dense=0.75, text="gamma passage"),
    ]

    _assess(candidates, question="the question text")

    prompt = fake_llm.calls[0][1]
    assert "the question text" in prompt
    assert "alpha" in prompt and "beta" in prompt and "gamma" not in prompt
    assert "alpha " * 30 not in prompt
    assert fake_llm.token_caps == [settings.SUFFICIENCY_LLM_MAX_OUTPUT_TOKENS]


@pytest.mark.parametrize(
    "reply",
    [
        RuntimeError("quota"),
        "not json at all",
        '{"sufficient": "yes", "reason": "x"}',
        '{"reason": "missing verdict"}',
        '["sufficient"]',
        "",
    ],
)
def test_llm_failure_or_bad_json_falls_back(fake_llm, reply):
    fake_llm.replies[STAGE] = reply

    result = _assess([_candidate("a", dense=0.85)])

    assert result.method == "fallback"
    assert result.sufficient is True  # above the 0.8 midpoint of 0.7 and 0.9


def test_fallback_leans_insufficient_below_the_midpoint(fake_llm):
    fake_llm.replies[STAGE] = RuntimeError("down")

    result = _assess([_candidate("a", dense=0.75)])

    assert result.method == "fallback" and not result.sufficient


def test_disabled_llm_stage_follows_the_score_lean(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "SUFFICIENCY_LLM_ENABLED", False)

    above = _assess([_candidate("a", dense=0.85)])
    below = _assess([_candidate("a", dense=0.75)])

    assert (above.sufficient, above.method) == (True, "score")
    assert (below.sufficient, below.method) == (False, "score")
    assert fake_llm.calls == []


def test_candidates_without_dense_scores_go_to_the_llm(fake_llm):
    fake_llm.replies[STAGE] = json.dumps({"sufficient": True, "reason": "keyword evidence"})

    result = _assess([_candidate("a", keyword=12.0)])

    assert result.method == "llm" and result.sufficient


def test_scores_use_the_best_dense_and_keyword_values():
    candidates = [
        Candidate("a", "t", {}, 0.1, provenance=[Contribution("vector", 1, "q1", 0.7), Contribution("vector", 2, "q2", 0.9)]),
        _candidate("b", dense=0.8, keyword=3.0),
        _candidate("c", dense=0.6, keyword=11.5),
        _candidate("d", dense=0.5),
    ]

    scores = compute_scores(candidates)

    assert scores.top_dense == 0.9
    assert scores.mean_top3_dense == pytest.approx((0.9 + 0.8 + 0.6) / 3)
    assert scores.top_keyword == 11.5
    assert compute_scores([]).top_dense is None


def test_every_call_logs_verdict_method_and_reason(fake_llm, caplog):
    fake_llm.replies[STAGE] = json.dumps({"sufficient": False, "reason": "only shares a topic"})

    with caplog.at_level(logging.INFO, logger="app.query.sufficiency"):
        _assess([_candidate("a", dense=0.8)])
        _assess([_candidate("a", dense=0.95)])

    messages = [r.getMessage() for r in caplog.records if r.name == "app.query.sufficiency"]
    assert len(messages) == 2
    assert "method=llm" in messages[0] and "only shares a topic" in messages[0] and "sufficient=False" in messages[0]
    assert "method=score" in messages[1] and "sufficient=True" in messages[1]
