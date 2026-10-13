"""The document viewer API: passages per document, page images, and where originals are kept."""

import io

import pymupdf
import pytest
from PIL import Image

from app.api.routes import viewer
from app.shared import document_files
from app.shared.schemas.chunk import Chunk
from app.shared.session_store import PERSISTENT_SCOPE

PAGE_TEXT = "The combined nameplate capacity is 445,000 barrels per day."


class _FakeStore:
    def __init__(self, chunks):
        self.chunks = chunks

    def all_chunks(self):
        return self.chunks

    def get(self, chunk_ids):
        return [chunk for chunk in self.chunks if chunk.chunk_id in chunk_ids]

    def count(self):
        return len(self.chunks)


def _chunk(chunk_id, source_doc, page, text=PAGE_TEXT, method="text", chunk_type="text"):
    return Chunk(
        chunk_id=chunk_id, text=text, source_doc=source_doc, page=page, chunk_type=chunk_type,
        extraction_method=method, corpus_scope=PERSISTENT_SCOPE,
    )


def _pdf_bytes(pages=2) -> bytes:
    document = pymupdf.open()
    for number in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), PAGE_TEXT if number == 0 else "Second page text.")
    return document.tobytes()


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore([
        _chunk("c2", "report.pdf", 2, text="Second page text."),
        _chunk("c1", "report.pdf", 1),
        _chunk("v1", "report.pdf", 1, text="| a | b |", method="vision", chunk_type="table"),
        _chunk("d1", "notes.docx", None, text="A paragraph from a DOCX file."),
        _chunk("x1", "other.pdf", 1),
    ])
    monkeypatch.setattr(viewer, "get_vector_store", lambda: fake)
    return fake


@pytest.fixture
def originals():
    document_files.save(PERSISTENT_SCOPE, "report.pdf", _pdf_bytes())
    document_files.save(PERSISTENT_SCOPE, "chart.png", _png_bytes())
    document_files.save(PERSISTENT_SCOPE, "notes.docx", b"PK-not-a-real-docx")


def _is_png(content: bytes) -> bool:
    return content.startswith(b"\x89PNG")


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #
def test_document_lists_its_own_passages_in_page_order(client, store, originals):
    body = client.get("/api/viewer/document", params={"source_doc": "report.pdf"}).json()

    assert body["kind"] == "pdf"
    assert body["page_count"] == 2
    assert [p["chunk_id"] for p in body["passages"]] == ["c1", "v1", "c2"]
    assert body["passages"][1]["chunk_type"] == "table"
    assert body["passages"][1]["extraction_method"] == "vision"


def test_a_docx_has_passages_but_no_pages(client, store, originals):
    body = client.get("/api/viewer/document", params={"source_doc": "notes.docx"}).json()

    assert (body["kind"], body["page_count"]) == ("docx", 0)
    assert body["passages"][0]["text"] == "A paragraph from a DOCX file."


def test_a_document_without_its_original_still_shows_passages(client, store):
    body = client.get("/api/viewer/document", params={"source_doc": "report.pdf"}).json()

    assert (body["kind"], body["page_count"]) == ("unavailable", 0)
    assert len(body["passages"]) == 3


def test_an_unknown_document_is_not_found(client, store):
    response = client.get("/api/viewer/document", params={"source_doc": "nope.pdf"})

    assert response.status_code == 404


def test_a_malformed_session_id_is_rejected(client, store):
    response = client.get("/api/viewer/document", params={"source_doc": "report.pdf", "session_id": "../x"})

    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Page images
# --------------------------------------------------------------------------- #
def test_a_pdf_page_is_rendered_as_png(client, store, originals):
    response = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 2})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert _is_png(response.content)


def test_the_cited_passage_changes_the_rendered_page(client, store, originals):
    plain = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1})
    marked = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1, "chunk_id": "c1"})

    assert _is_png(marked.content)
    assert marked.content != plain.content


def test_a_vision_passage_is_not_searched_for_on_the_page(client, store, originals):
    plain = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1})
    vision = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1, "chunk_id": "v1"})

    assert vision.content == plain.content


def test_a_passage_from_another_page_or_document_is_ignored(client, store, originals):
    plain = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1})
    other_page = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1, "chunk_id": "c2"})
    other_doc = client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 1, "chunk_id": "x1"})

    assert other_page.content == plain.content
    assert other_doc.content == plain.content


def test_an_image_document_is_served_as_itself(client, store, originals):
    response = client.get("/api/viewer/page", params={"source_doc": "chart.png"})

    assert response.headers["content-type"] == "image/png"
    assert response.content == _png_bytes()


@pytest.mark.parametrize("params", [
    {"source_doc": "report.pdf", "page": 3},
    {"source_doc": "notes.docx", "page": 1},
    {"source_doc": "missing.pdf", "page": 1},
])
def test_pages_that_do_not_exist_are_not_found(client, store, originals, params):
    assert client.get("/api/viewer/page", params=params).status_code == 404


def test_page_zero_fails_validation(client, store, originals):
    assert client.get("/api/viewer/page", params={"source_doc": "report.pdf", "page": 0}).status_code == 422


# --------------------------------------------------------------------------- #
# Originals on disk
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["../secret.txt", "..\\secret.txt", "sub/dir.pdf", "", ".."])
def test_a_name_with_a_path_in_it_is_never_read_or_written(name, tmp_path):
    document_files.save(PERSISTENT_SCOPE, name, b"data")

    assert document_files.find(PERSISTENT_SCOPE, name) is None
    assert not (tmp_path / "secret.txt").exists()


def test_the_fetched_corpus_is_the_fallback_for_originals(tmp_path):
    corpus = tmp_path / "corpus-files"
    corpus.mkdir()
    (corpus / "fetched.pdf").write_bytes(_pdf_bytes(1))

    assert document_files.find(PERSISTENT_SCOPE, "fetched.pdf") == (corpus / "fetched.pdf").resolve()


def test_deleting_removes_the_uploaded_original_only(tmp_path):
    corpus = tmp_path / "corpus-files"
    corpus.mkdir()
    (corpus / "report.pdf").write_bytes(b"fetched copy")
    document_files.save(PERSISTENT_SCOPE, "report.pdf", b"uploaded copy")

    document_files.delete(PERSISTENT_SCOPE, "report.pdf")

    assert document_files.find(PERSISTENT_SCOPE, "report.pdf").read_bytes() == b"fetched copy"
