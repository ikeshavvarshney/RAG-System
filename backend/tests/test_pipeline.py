import io
from unittest.mock import patch

import docx as docx_lib
import pymupdf

from app.ingestion import pipeline as pipeline_mod
from app.ingestion.pipeline import ingest_files


def _make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_docx(text: str) -> bytes:
    document = docx_lib.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_batch_with_bad_files_still_processes_valid_ones():
    files = [
        ("good.pdf", _make_pdf("A perfectly valid PDF with enough real text content here.")),
        ("corrupt.pdf", b"%PDF-1.4\nthis is not a real pdf structure"),
        ("notes.txt", b"plain text file, unsupported extension"),
        ("good.docx", _make_docx("A perfectly valid DOCX paragraph with real content.")),
    ]

    result = ingest_files(files, corpus_scope="persistent")

    assert len(result.failed) == 2
    failed_names = {f.filename for f in result.failed}
    assert failed_names == {"corrupt.pdf", "notes.txt"}

    assert "good.pdf" in result.succeeded
    assert "good.docx" in result.succeeded
    assert len(result.chunks) > 0


def test_every_chunk_has_correct_corpus_scope():
    files = [
        ("good.pdf", _make_pdf("A perfectly valid PDF with enough real text content here.")),
    ]

    result = ingest_files(files, corpus_scope="session:abc123")

    assert len(result.chunks) > 0
    for chunk in result.chunks:
        assert chunk["corpus_scope"] == "session:abc123"


def test_all_valid_files_succeed_with_no_failures():
    files = [
        ("a.pdf", _make_pdf("Valid content for the first document, plenty of words.")),
        ("b.docx", _make_docx("Valid content for the second document, plenty of words.")),
    ]

    result = ingest_files(files, corpus_scope="persistent")

    assert len(result.failed) == 0
    assert len(result.succeeded) == 2


# --------------------------------------------------------------------------- #
# Vision chunk_type preservation: chart / image_caption pieces bypass split(),
# text / table pieces continue through it.
# --------------------------------------------------------------------------- #
def _run_with_pieces(pieces: list[dict], corpus_scope: str = "persistent"):
    with patch.object(pipeline_mod, "route_file", return_value=pieces):
        return ingest_files([("fig.pdf", b"stub")], corpus_scope=corpus_scope)


def test_vision_chart_piece_survives_as_single_chart_chunk():
    piece = {
        "page": 4,
        "location": None,
        "text": (
            "Chart type: bar chart. Title: Widgets shipped by year. "
            "X-axis: year (2019-2021). Data points: 2019 = 10, 2020 = 20, 2021 = 25."
        ),
        "extraction_method": "vision",
        "chunk_type": "chart",
    }

    result = _run_with_pieces([piece])

    assert result.succeeded == ["fig.pdf"]
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk["chunk_type"] == "chart"  # not reclassified to "text"
    assert chunk["extraction_method"] == "vision"
    assert chunk["page"] == 4
    assert chunk["source_doc"] == "fig.pdf"
    assert chunk["corpus_scope"] == "persistent"
    assert chunk["text"] == piece["text"]
    assert chunk["chunk_id"]


def test_vision_image_caption_piece_survives_as_single_caption_chunk():
    piece = {
        "page": 1,
        "location": "Figure 2",
        "text": "A schematic diagram of a centrifugal pump with labelled inlet and outlet.",
        "extraction_method": "vision",
        "chunk_type": "image_caption",
    }

    result = _run_with_pieces([piece])

    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk["chunk_type"] == "image_caption"
    assert chunk["extraction_method"] == "vision"
    assert chunk["location"] == "Figure 2"
    assert chunk["text"] == piece["text"]


def test_vision_table_piece_still_goes_through_split_and_stays_table():
    md_table = (
        "| Year | Value |\n| --- | --- |\n| 2019 | 10 |\n| 2020 | 20 |\n| 2021 | 25 |"
    )
    piece = {
        "page": 2,
        "location": None,
        "text": md_table,
        "extraction_method": "vision",
        "chunk_type": "table",
    }

    result = _run_with_pieces([piece])

    table_chunks = [c for c in result.chunks if c["chunk_type"] == "table"]
    assert len(table_chunks) == 1
    assert table_chunks[0]["text"].strip() == md_table.strip()
    assert table_chunks[0]["extraction_method"] == "vision"
    assert table_chunks[0]["corpus_scope"] == "persistent"


def test_vision_plain_text_piece_still_goes_through_split():
    piece = {
        "page": 1,
        "location": None,
        "text": "This is a sentence of transcribed body text that repeats. " * 60,
        "extraction_method": "ocr",
        "chunk_type": "text",
    }

    result = _run_with_pieces([piece])

    assert len(result.chunks) > 1  # long text was chunked, not wrapped whole
    assert all(c["chunk_type"] == "text" for c in result.chunks)
    assert all(c["extraction_method"] == "ocr" for c in result.chunks)