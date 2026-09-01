import io

import docx as docx_lib
import pymupdf

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