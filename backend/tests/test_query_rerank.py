import pytest

from app.core.config import settings
from app.query import rerank
from app.query.fusion import Candidate, Contribution
from app.query.rerank import consolidate, rerank_and_consolidate


class FakeCrossEncoder:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs):
        self.pairs.extend(pairs)
        return [self.scores.get(text, 0.0) for _, text in pairs]


def _candidate(chunk_id: str, text: str, score: float = 0.1, **metadata) -> Candidate:
    return Candidate(
        chunk_id,
        text,
        {"source_doc": f"{chunk_id}.pdf", "page": 3, "extraction_method": "vision", **metadata},
        score,
        provenance=[Contribution("vector", 1, "q")],
    )


@pytest.fixture
def model(monkeypatch):
    fake = FakeCrossEncoder({"gamma passage": 9.0, "beta passage": 5.0, "alpha passage": 1.0})
    monkeypatch.setattr(rerank, "_model", fake)
    return fake


def _fused() -> list[Candidate]:
    return [
        _candidate("a", "alpha passage", 0.03),
        _candidate("b", "beta passage", 0.02),
        _candidate("c", "gamma passage", 0.01),
    ]


def test_rerank_reorders_by_cross_encoder_score(model):
    context = rerank_and_consolidate("question", _fused())

    assert context.reranked
    assert [p.chunk_id for p in context.passages] == ["c", "b", "a"]


def test_rerank_scores_against_the_original_question(model):
    rerank_and_consolidate("what the user asked", _fused())

    assert {q for q, _ in model.pairs} == {"what the user asked"}


def test_metadata_and_provenance_survive(model):
    web = Candidate(
        "w", "beta passage", {"source_url": "https://example.com", "title": "Ex"}, 0.02, source="web"
    )
    context = rerank_and_consolidate("q", [_candidate("a", "alpha passage"), web])

    by_id = {p.chunk_id: p for p in context.passages}
    assert by_id["a"].metadata == {"source_doc": "a.pdf", "page": 3, "extraction_method": "vision"}
    assert by_id["a"].provenance == [Contribution("vector", 1, "q")]
    assert by_id["w"].source == "web"
    assert by_id["w"].metadata["source_url"] == "https://example.com"


def test_failure_falls_back_to_fused_order(monkeypatch):
    class Broken:
        def predict(self, pairs):
            raise RuntimeError("model missing")

    monkeypatch.setattr(rerank, "_model", Broken())
    context = rerank_and_consolidate("q", _fused())

    assert not context.reranked
    assert [p.chunk_id for p in context.passages] == ["a", "b", "c"]


def test_only_the_shortlist_is_scored(model, monkeypatch):
    monkeypatch.setattr(settings, "RERANK_CANDIDATES", 2)
    rerank_and_consolidate("q", _fused())

    assert len(model.pairs) == 2


def test_consolidate_drops_near_duplicates():
    passages, _ = consolidate(
        [
            _candidate("a", "Revenue grew 12% in the third quarter."),
            _candidate("b", "revenue grew 12% in the third quarter"),
            _candidate("c", "Costs fell."),
        ],
        top_k=10,
        token_budget=1000,
    )

    assert [p.chunk_id for p in passages] == ["a", "c"]


def test_consolidate_respects_top_k_and_token_budget():
    candidates = [_candidate(str(i), f"word{i} " * 50) for i in range(6)]

    by_k, _ = consolidate(candidates, top_k=2, token_budget=10_000)
    by_budget, used = consolidate(candidates, top_k=10, token_budget=210)

    assert len(by_k) == 2
    assert 0 < used <= 210
    assert len(by_budget) < 6


def test_empty_input_skips_the_model(monkeypatch):
    monkeypatch.setattr(rerank, "_load_model", lambda: pytest.fail("model should not load"))

    context = rerank_and_consolidate("q", [])

    assert context.passages == [] and not context.reranked
