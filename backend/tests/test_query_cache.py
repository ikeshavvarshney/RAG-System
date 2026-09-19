import asyncio
import math

import pytest

from app.core.config import settings
from app.ingestion import embedder
from app.query import cache, history
from app.shared.schemas.citation import CorpusCitation, WebCitation

MARS = "What was NASA's FY23 Mars Sample Return funding?"
MARS_PARAPHRASE = "How much money did NASA request for Mars Sample Return in FY23?"
LANDSAT = "What improvements will Landsat Next bring?"
BUDGET_2024 = "What was NASA's budget in 2024?"
BUDGET_2023 = "What was NASA's budget in 2023?"
LANDSAT_2024 = "What is the Landsat Next launch cost in 2024?"
LUNAR_23 = "What was NASA's FY23 budget for lunar missions?"
MARS_23 = "What was NASA's FY23 budget for Mars missions?"
YEAR_2023 = "What was NASA's budget request for FY 2023?"
YEAR_2024 = "What was NASA's budget request for FY 2024?"
YEAR_2024_PARAPHRASE = "How much did NASA request in its FY 2024 budget?"

VECTORS = {
    MARS: [1.0, 0.0, 0.0, 0.0],
    MARS_PARAPHRASE: [0.98, 0.15, 0.0, 0.0],
    LANDSAT: [0.0, 1.0, 0.0, 0.0],
    BUDGET_2024: [0.0, 0.0, 1.0, 0.0],
    BUDGET_2023: [0.0, 0.0, 0.92, 0.39],
    LANDSAT_2024: [0.0, 0.3, 0.1, 0.95],
    LUNAR_23: [0.0, 0.0, 0.92, 0.39],
    MARS_23: [0.0, 0.0, 1.0, 0.0],
    YEAR_2023: [1.0, 0.0, 0.0, 0.0],
    YEAR_2024: [0.9, 0.05, 0.0, 0.0],
    YEAR_2024_PARAPHRASE: [0.98, 0.0, 0.0, 0.0],
}

CITATIONS = [CorpusCitation(source_doc="nasa.pdf", page=2, chunk_id="c1")]


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


@pytest.fixture
def fake_embeddings(monkeypatch):
    def embed(texts: list[str]) -> list[list[float]]:
        return [_unit(VECTORS.get(text, [0.0, 0.0, 0.0, 1.0])) for text in texts]

    monkeypatch.setattr(cache, "embed_queries", embed)


def _seed(resolved=MARS, scope="persistent", citations=CITATIONS, answer="$822M"):
    cache.put(resolved, resolved, answer, citations, scope)


@pytest.mark.usefixtures("fake_embeddings")
class TestLookup:
    def test_equivalent_rephrase_hits_and_returns_the_stored_entry(self):
        _seed()

        hit = cache.lookup(MARS_PARAPHRASE, "persistent")

        assert hit is not None
        assert hit.answer == "$822M"
        assert hit.citations == CITATIONS
        assert hit.resolved_question == MARS
        assert hit.raw_question == MARS
        assert hit.cited_docs == ["nasa.pdf"]
        assert hit.corpus_scope == "persistent"
        assert hit.score >= settings.CACHE_SIMILARITY_THRESHOLD
        assert hit.created_at > 0

    def test_identical_question_hits(self):
        _seed()

        assert cache.lookup(MARS, "persistent").score == pytest.approx(1.0)

    def test_unrelated_question_misses(self):
        _seed()

        assert cache.lookup(LANDSAT, "persistent") is None

    def test_subtly_different_question_misses_at_the_default_threshold(self):
        _seed(MARS_23)

        assert cache.lookup(LUNAR_23, "persistent") is None

    def test_high_similarity_is_not_enough_when_the_numbers_differ(self):
        _seed(YEAR_2023)

        assert cache.lookup(YEAR_2024, "persistent") is None
        assert cache.lookup(YEAR_2023, "persistent") is not None

    def test_a_later_candidate_with_matching_numbers_is_still_found(self):
        _seed(YEAR_2023, answer="fy23")
        _seed(YEAR_2024, answer="fy24")

        hit = cache.lookup(YEAR_2024_PARAPHRASE, "persistent")

        assert hit is not None and hit.answer == "fy24"

    def test_number_formatting_does_not_defeat_a_match(self):
        _seed("What was the $1,200 million request?")

        assert cache.lookup("What was the $1200 million request?", "persistent") is not None

    def test_default_threshold_is_conservative(self):
        assert settings.CACHE_SIMILARITY_THRESHOLD >= 0.95

    def test_threshold_comes_from_config(self, monkeypatch):
        _seed(MARS_23)
        monkeypatch.setattr(settings, "CACHE_SIMILARITY_THRESHOLD", 0.9)

        assert cache.lookup(LUNAR_23, "persistent") is not None

    def test_session_entry_is_never_returned_for_the_persistent_scope(self):
        _seed(scope="session:abc")

        assert cache.lookup(MARS, "persistent") is None
        assert cache.lookup(MARS, "session:abc") is not None
        assert cache.lookup(MARS, "session:other") is None

    def test_scope_is_filtered_inside_the_query_not_after_it(self):
        cache.put(MARS, MARS, "session answer", CITATIONS, "session:abc")
        cache.put(MARS_PARAPHRASE, MARS_PARAPHRASE, "corpus answer", CITATIONS, "persistent")

        hit = cache.lookup(MARS, "persistent")

        assert hit is not None and hit.answer == "corpus answer"

    def test_two_conversations_asking_the_same_follow_up_do_not_collide(self, fake_llm):
        fake_llm.replies["query_history"] = lambda prompt: (
            BUDGET_2024 if "NASA" in prompt else LANDSAT_2024
        )
        history.record_turn("a", "NASA budget?", "What is NASA's budget?", "$25B")
        history.record_turn("b", "Landsat cost?", "What does Landsat Next cost?", "$1B")

        _, resolved_a = asyncio.run(history.resolve_question("a", "what about 2024?"))
        _, resolved_b = asyncio.run(history.resolve_question("b", "what about 2024?"))
        cache.put("what about 2024?", resolved_a, "NASA answer", CITATIONS, "persistent")

        assert resolved_a != resolved_b
        assert cache.lookup(resolved_b, "persistent") is None
        assert cache.lookup(resolved_a, "persistent").answer == "NASA answer"

    def test_put_overwrites_the_same_question_and_scope(self):
        _seed(answer="old")
        _seed(answer="new")

        assert cache.get_answer_cache().count() == 1
        assert cache.lookup(MARS, "persistent").answer == "new"

    def test_web_citations_round_trip_and_are_not_document_ids(self):
        web = WebCitation(source_url="https://nasa.gov/x", title="NASA")
        _seed(citations=[web, *CITATIONS])

        hit = cache.lookup(MARS, "persistent")

        assert hit.citations == [web, *CITATIONS]
        assert hit.cited_docs == ["nasa.pdf"]

    def test_lookup_on_an_empty_cache_makes_no_embedding_call(self, monkeypatch):
        def fail(texts):
            raise AssertionError("embedded on an empty cache")

        monkeypatch.setattr(cache, "embed_queries", fail)

        assert cache.lookup(MARS, "persistent") is None


@pytest.mark.usefixtures("fake_embeddings")
class TestInvalidation:
    def test_by_document_removes_only_entries_citing_it(self):
        _seed(MARS, citations=[CorpusCitation(source_doc="a.pdf", chunk_id="c1")])
        _seed(LANDSAT, citations=[CorpusCitation(source_doc="b.pdf", chunk_id="c2")])
        _seed(
            BUDGET_2024,
            citations=[
                CorpusCitation(source_doc="a.pdf", chunk_id="c3"),
                CorpusCitation(source_doc="b.pdf", chunk_id="c4"),
            ],
        )

        assert cache.invalidate_by_document("a.pdf") == 2

        assert cache.lookup(MARS, "persistent") is None
        assert cache.lookup(BUDGET_2024, "persistent") is None
        assert cache.lookup(LANDSAT, "persistent") is not None

    def test_by_document_can_be_limited_to_a_scope(self):
        doc = [CorpusCitation(source_doc="report.pdf", chunk_id="c1")]
        _seed(MARS, scope="persistent", citations=doc)
        _seed(MARS, scope="session:abc", citations=doc)

        assert cache.invalidate_by_document("report.pdf", scope="session:abc") == 1

        assert cache.lookup(MARS, "session:abc") is None
        assert cache.lookup(MARS, "persistent") is not None

    def test_by_document_matches_exact_names_with_unusual_characters(self):
        _seed(MARS, citations=[CorpusCitation(source_doc="my report (v2).pdf", chunk_id="c1")])
        _seed(LANDSAT, citations=[CorpusCitation(source_doc="my report (v2).pdf.bak", chunk_id="c2")])

        assert cache.invalidate_by_document("my report (v2).pdf") == 1
        assert cache.lookup(LANDSAT, "persistent") is not None

    def test_unknown_document_removes_nothing(self):
        _seed()

        assert cache.invalidate_by_document("missing.pdf") == 0
        assert cache.lookup(MARS, "persistent") is not None

    def test_scope_removes_that_scope_only(self):
        _seed(MARS, scope="session:abc")
        _seed(LANDSAT, scope="session:abc")
        _seed(MARS, scope="persistent")

        assert cache.invalidate_scope("session:abc") == 2

        assert cache.lookup(MARS, "session:abc") is None
        assert cache.lookup(MARS, "persistent") is not None
        assert cache.invalidate_scope("session:abc") == 0


def test_resolved_question_is_embedded_once_across_cache_lookup_and_retrieval(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_DIR", str(tmp_path / "embeddings"))
    sent: list[list[str]] = []

    def embed_batch(texts, model):
        sent.append(list(texts))
        return [[1.0, 0.5, 0.2] for _ in texts]

    monkeypatch.setattr(embedder._client, "embed_batch", embed_batch)
    cache.put("seed", "a seeded question", "answer", [], "persistent")
    sent.clear()

    resolved = "What did NASA request for Mars Sample Return?"
    cache.lookup(resolved, "persistent")
    embedder.embed_queries([resolved, "Mars Sample Return budget", "MSR funding request"])

    assert sent == [[resolved], ["Mars Sample Return budget", "MSR funding request"]]
