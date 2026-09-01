import pymupdf

from app.ingestion.extractors.pdf import extract


def _make_pdf_with_text(pages_text: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_text_layer_pdf_extracts_per_page_with_page_numbers():
    pdf_bytes = _make_pdf_with_text([
        "This is the first page with plenty of readable text content.",
        "This is the second page, also with plenty of readable text.",
    ])

    result = extract(pdf_bytes, "sample.pdf")

    assert len(result) == 2
    assert result[0]["page"] == 1
    assert result[1]["page"] == 2
    assert "first page" in result[0]["text"]
    assert "second page" in result[1]["text"]


def test_normal_pages_tagged_as_text():
    pdf_bytes = _make_pdf_with_text([
        "A reasonably long paragraph of real text content on this page.",
    ])

    result = extract(pdf_bytes, "sample.pdf")

    assert result[0]["extraction_method"] == "text"


def test_blank_page_flagged_as_scanned():
    doc = pymupdf.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract(pdf_bytes, "blank.pdf")

    assert result[0]["extraction_method"] == "scanned"


def test_page_count_matches_document():
    pdf_bytes = _make_pdf_with_text(["Page one text.", "Page two text.", "Page three text."])

    result = extract(pdf_bytes, "sample.pdf")

    assert len(result) == 3