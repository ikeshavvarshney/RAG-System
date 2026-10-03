import asyncio
from contextlib import contextmanager

import pytest

from app.query import generation, llm
from app.query.fusion import Candidate
from app.query.generation import NOT_IN_CONTEXT, generate_answer, parse_markers


def _corpus(chunk_id: str, doc: str = "report.pdf", page: int = 3) -> Candidate:
    meta = {"source_doc": doc, "page": page, "chunk_type": "text"}
    return Candidate(chunk_id, f"text of {chunk_id}", meta, 0.5)


def _web(chunk_id: str, url: str) -> Candidate:
    meta = {"source_type": "web", "source_url": url, "title": "T"}
    return Candidate(chunk_id, f"web text {chunk_id}", meta, 0.4, source="web")


@pytest.fixture
def fake_model(monkeypatch):
    class Fake:
        reply = ""
        prompts: list[str] = []

        async def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return self.reply

    fake = Fake()
    fake.prompts = []
    monkeypatch.setattr(generation, "_call", fake)
    return fake


def _run(question="q?", passages=None, **kwargs):
    return asyncio.run(generate_answer(question, passages, **kwargs))


def test_corpus_answer_marker_resolves_to_a_passage(fake_model):
    fake_model.reply = "Revenue grew 10% [1]."
    result = _run(passages=[_corpus("a"), _corpus("b", "other.pdf", 7)])

    assert result.cited_markers == [1]
    assert result.passages[result.cited_markers[0]].source_doc == "report.pdf"
    assert not result.is_non_answer and result.invalid_markers == []
    assert "[1] report.pdf, page 3" in fake_model.prompts[0]


def test_non_answer_is_detected(fake_model):
    fake_model.reply = NOT_IN_CONTEXT
    result = _run(passages=[_corpus("a")])

    assert result.is_non_answer and result.cited_markers == []


def test_no_passages_skips_the_model(fake_model):
    result = _run(passages=[])

    assert result.is_non_answer and result.answer == NOT_IN_CONTEXT
    assert fake_model.prompts == []


def test_out_of_range_marker_is_invalid(fake_model):
    fake_model.reply = "A [1] and B [7] and C [0]."
    result = _run(passages=[_corpus(c) for c in "abcde"])

    assert result.cited_markers == [1]
    assert result.invalid_markers == [7, 0]


def test_duplicate_and_combined_markers_parse():
    assert parse_markers("x [1][2] y [1, 2] z [2] [3;1]", 3) == ([1, 2, 3], [])
    assert parse_markers("no markers here", 3) == ([], [])
    assert parse_markers("see [9, 1][9]", 2) == ([1], [9])


def test_decomposed_numbering_is_global_and_deduplicated(fake_model):
    fake_model.reply = "Both [1][3][4]."
    shared = _corpus("shared")
    result = _run(
        "orig?",
        sub_contexts=[("sub one?", [shared, _corpus("a")]), ("sub two?", [_corpus("b"), shared, _corpus("c")])],
    )
    prompt = fake_model.prompts[0]

    assert [result.passages[n].chunk_id for n in sorted(result.passages)] == ["shared", "a", "b", "c"]
    assert prompt.index("sub one?") < prompt.index("[2] report.pdf") < prompt.index("sub two?") < prompt.index("[3] report.pdf")
    assert prompt.count("text of shared") == 2
    assert "orig?" in prompt and "ONE coherent answer" in prompt
    assert result.cited_markers == [1, 3, 4]


def test_web_passage_label_uses_source_url(fake_model):
    fake_model.reply = "Per the web [1]."
    result = _run(passages=[_web("w", "https://example.com/x")])

    assert "[1] https://example.com/x" in fake_model.prompts[0]
    assert result.passages[1].source_url == "https://example.com/x"
    assert result.passages[1].source_doc is None


def test_stage_event_emitted_and_usage_recorded(monkeypatch):
    tracker = llm._client.tracker

    def fake_generate(stage, model, prompt, config=None):
        tracker.record(stage, model, 100, 20)
        return "Fact [1]."

    monkeypatch.setattr(llm._client, "generate", fake_generate)
    events = []

    @contextmanager
    def stage(name):
        events.append(name)
        yield

    result = _run(passages=[_corpus("a")], stage=stage)

    assert events == ["generation"]
    assert result.usage.prompt_tokens == 100 and result.usage.total_tokens == 120
