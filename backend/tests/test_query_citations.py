from contextlib import contextmanager

from pydantic import TypeAdapter

from app.query.citations import MIN_SUBSTANTIVE_WORDS, NO_SUPPORT_ANSWER, filter_structural
from app.query.generation import NOT_IN_CONTEXT, GenerationResult, PassageRef
from app.shared.schemas.citation import Citation, CorpusCitation, WebCitation

CLAIM = "Revenue grew strongly across every region during the last fiscal year"


def _corpus(n: int, doc: str = "report.pdf", page: int | None = 1, score: float = 0.5) -> PassageRef:
    return PassageRef(
        number=n, chunk_id=f"c{n}", kind="corpus", source_doc=doc, page=page, score=score, snippet=f"text {n}"
    )


def _web(n: int, url: str = "https://example.com/a", score: float = 0.4) -> PassageRef:
    return PassageRef(number=n, chunk_id=f"w{n}", kind="web", source_url=url, title="Title", score=score)


def _gen(answer: str, *refs: PassageRef, non_answer: bool = False) -> GenerationResult:
    return GenerationResult(answer=answer, passages={r.number: r for r in refs}, is_non_answer=non_answer)


def test_valid_answer_yields_typed_citations_that_resolve():
    result = filter_structural(_gen(f"{CLAIM} [1]. {CLAIM} again [2].", _corpus(1), _web(2)))

    assert [type(c) for c in result.citations] == [CorpusCitation, WebCitation]
    assert result.answer == f"{CLAIM} [1]. {CLAIM} again [2]."
    assert result.marker_map == {1: 1, 2: 2}
    assert result.citations[0].snippet == "text 1" and result.citations[0].score == 0.5
    assert not result.removed_claims and not result.is_non_answer


def test_out_of_range_marker_is_stripped_and_reported():
    passages = [_corpus(n, page=n) for n in range(1, 6)]
    result = filter_structural(_gen(f"{CLAIM} [1][7].", *passages))

    assert result.answer == f"{CLAIM} [1]."
    assert result.invalid_markers == [7]
    assert [(c.text, c.reason) for c in result.removed_claims] == [("[7]", "invalid_marker")]


def test_zero_marker_is_invalid():
    result = filter_structural(_gen(f"{CLAIM} [0][1].", _corpus(1)))

    assert result.invalid_markers == [0] and result.answer == f"{CLAIM} [1]."


def test_uncited_substantive_paragraph_is_removed_with_a_note():
    uncited = "Margins also widened because of lower input costs and better pricing power"
    result = filter_structural(_gen(f"{CLAIM} [1].\n\n{uncited}.", _corpus(1)))

    assert result.answer == f"{CLAIM} [1]."
    assert [(c.text, c.reason) for c in result.removed_claims] == [(f"{uncited}.", "uncited")]


def test_claim_whose_only_marker_is_invalid_is_removed_as_uncited():
    result = filter_structural(_gen(f"{CLAIM} [1].\n\n{CLAIM} too [9].", _corpus(1)))

    assert result.answer == f"{CLAIM} [1]."
    assert {c.reason for c in result.removed_claims} == {"invalid_marker", "uncited"}


def test_short_connective_line_and_heading_are_kept():
    assert len("In summary:".split()) < MIN_SUBSTANTIVE_WORDS
    result = filter_structural(_gen(f"## Overview\n\nIn summary:\n\n{CLAIM} [1].", _corpus(1)))

    assert "## Overview" in result.answer and "In summary:" in result.answer
    assert not result.removed_claims


def test_not_in_context_passes_through_untouched():
    result = filter_structural(_gen(NOT_IN_CONTEXT, _corpus(1), non_answer=True))

    assert result.answer == NOT_IN_CONTEXT
    assert result.is_non_answer and result.citations == [] and result.removed_claims == []


def test_everything_removed_gives_the_honest_non_answer():
    result = filter_structural(_gen(f"{CLAIM} without support.", _corpus(1)))

    assert result.answer == NO_SUPPORT_ANSWER
    assert result.is_non_answer and result.citations == []
    assert result.removed_claims[0].reason == "uncited"


def test_citation_union_discriminates_and_round_trips():
    adapter = TypeAdapter(Citation)
    corpus = adapter.validate_python(
        {"kind": "corpus", "source_doc": "a.pdf", "page": 2, "chunk_id": "c", "score": 0.9, "snippet": "s"}
    )
    web = adapter.validate_python({"kind": "web", "source_url": "https://x.io", "title": "T"})

    assert isinstance(corpus, CorpusCitation) and isinstance(web, WebCitation)
    assert adapter.validate_python(corpus.model_dump()) == corpus
    assert adapter.validate_python(web.model_dump()) == web


def test_same_doc_and_page_collapse_to_the_best_scoring_citation_and_remap():
    refs = [_corpus(n, page=3, score=s) for n, s in zip(range(1, 6), [0.2, 0.9, 0.4, 0.3, 0.1])]
    other = _corpus(6, doc="other.pdf", page=1, score=0.5)
    answer = f"{CLAIM} [1][2]. {CLAIM} again [3, 4]. {CLAIM} third [6]. {CLAIM} fourth [5]."
    result = filter_structural(_gen(answer, *refs, other))

    assert [c.chunk_id for c in result.citations] == ["c2", "c6"]
    assert result.marker_map == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2}
    assert result.answer == f"{CLAIM} [1]. {CLAIM} again [1]. {CLAIM} third [2]. {CLAIM} fourth [1]."


def test_web_citations_dedupe_by_url():
    refs = [_web(1, "https://a.io", 0.3), _web(2, "https://a.io", 0.8), _web(3, "https://b.io")]
    result = filter_structural(_gen(f"{CLAIM} [1]. {CLAIM} more [2]. {CLAIM} also [3].", *refs))

    assert [c.source_url for c in result.citations] == ["https://a.io", "https://b.io"]
    assert result.citations[0].score == 0.8


def test_mixed_corpus_and_web_answer_keeps_both_types():
    result = filter_structural(_gen(f"{CLAIM} [2]. {CLAIM} elsewhere [1].", _corpus(1), _web(2)))

    assert [c.kind for c in result.citations] == ["web", "corpus"]
    assert result.answer == f"{CLAIM} [1]. {CLAIM} elsewhere [2]."


def test_stage_event_is_emitted():
    events = []

    @contextmanager
    def stage(name):
        events.append(name)
        yield

    filter_structural(_gen(f"{CLAIM} [1].", _corpus(1)), stage=stage)

    assert events == ["citations"]
