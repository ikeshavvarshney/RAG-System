import pytest
from pydantic import BaseModel, ValidationError

from app.shared.schemas.chunk import Chunk
from app.shared.schemas.citation import Citation, CorpusCitation, WebCitation


def test_valid_chunk_constructs():
    c = Chunk(
        chunk_id="abc123",
        text="hello world",
        source_doc="report.pdf",
        page=3,
        location="section 2",
        chunk_type="text",
        extraction_method="text",
        corpus_scope="persistent",
    )
    assert c.chunk_id == "abc123"


def test_invalid_extraction_method_raises():
    with pytest.raises(ValidationError):
        Chunk(
            chunk_id="abc123",
            text="hello world",
            source_doc="report.pdf",
            chunk_type="text",
            extraction_method="telepathy",
            corpus_scope="persistent",
        )


def test_citation_round_trip_and_discrimination():
    class Wrapper(BaseModel):
        citation: Citation

    corpus = Wrapper(citation=CorpusCitation(source_doc="a.pdf", page=1, chunk_id="x"))
    web = Wrapper(citation=WebCitation(source_url="https://example.com", title="Example"))

    corpus_back = Wrapper.model_validate_json(corpus.model_dump_json())
    web_back = Wrapper.model_validate_json(web.model_dump_json())

    assert isinstance(corpus_back.citation, CorpusCitation)
    assert isinstance(web_back.citation, WebCitation)