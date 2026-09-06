import io
import logging

import pymupdf
import pytest
from PIL import Image

from app.core.config import settings
from app.ingestion.extractors import vision
from app.ingestion.extractors.ocr import OCRUnavailable
from app.ingestion.extractors.pdf import extract as pdf_extract
from app.ingestion.pipeline import ingest_files


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _png(color: str = "gray", size: tuple[int, int] = (600, 800)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _pdf_all_text() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A whole page of ordinary readable body text content here.")
    out = doc.tobytes()
    doc.close()
    return out


def _pdf_text_then_image() -> bytes:
    """Page 1: real text. Page 2: no text, one full-page raster image."""
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "This first page has plenty of ordinary readable body text here.")
    p2 = doc.new_page()
    p2.insert_image(p2.rect, stream=_png())
    out = doc.tobytes()
    doc.close()
    return out


def _boom(**_kwargs):
    raise RuntimeError("500 backend error")


def _ocr_unavailable(_image):
    raise OCRUnavailable("tesseract missing")


@pytest.fixture(autouse=True)
def vision_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", str(tmp_path / "vision"))


# --------------------------------------------------------------------------- #
# page-selection heuristic
# --------------------------------------------------------------------------- #
def test_page_needs_vision_heuristic():
    assert vision.page_needs_vision("", 0.9) is True
    assert vision.page_needs_vision("x" * 5, 0.6) is True
    assert vision.page_needs_vision("x" * 500, 0.9) is False  # has a text layer
    assert vision.page_needs_vision("", 0.1) is False  # no large image


# --------------------------------------------------------------------------- #
# vision success -> structured chunk
# --------------------------------------------------------------------------- #
def test_table_image_yields_vision_table_chunk(monkeypatch):
    md = "CONTENT_TYPE: table\n| Year | Value |\n| --- | --- |\n| 2019 | 10 |\n| 2020 | 20 |"
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: md)

    piece = vision.extract_image(_png(), page=3, mime_type="image/png")

    assert piece["extraction_method"] == "vision"
    assert piece["chunk_type"] == "table"
    assert "| Year | Value |" in piece["text"]
    assert piece["page"] == 3


def test_chart_image_yields_vision_chart_chunk_with_datapoints(monkeypatch):
    response = (
        "CONTENT_TYPE: chart\n"
        "Chart type: bar chart. Title: Widgets shipped by year.\n"
        "X-axis: year (2019-2021). Y-axis: units shipped (0-30).\n"
        "Data points: 2019 = 10, 2020 = 20, 2021 = 25."
    )
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: response)

    piece = vision.extract_image(_png())

    assert piece["chunk_type"] == "chart"
    assert piece["extraction_method"] == "vision"
    assert "bar chart" in piece["text"].lower()
    assert "2020 = 20" in piece["text"]


def test_missing_content_type_tag_is_classified_heuristically(monkeypatch):
    monkeypatch.setattr(
        vision._client,
        "generate_vision",
        lambda **kw: "| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |",
    )

    piece = vision.extract_image(_png())

    assert piece["chunk_type"] == "table"
    assert piece["extraction_method"] == "vision"


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
def test_second_call_on_same_image_hits_cache(monkeypatch):
    calls = {"n": 0}

    def gen(**_kw):
        calls["n"] += 1
        return "CONTENT_TYPE: figure\nA schematic diagram of a centrifugal pump."

    monkeypatch.setattr(vision._client, "generate_vision", gen)
    img = _png(color="blue")

    first = vision.extract_image(img)
    second = vision.extract_image(img)

    assert calls["n"] == 1  # zero API calls on the second pass
    assert first["text"] == second["text"]
    assert second["chunk_type"] == "image_caption"
    assert second["extraction_method"] == "vision"


# --------------------------------------------------------------------------- #
# OCR fallback (D-07)
# --------------------------------------------------------------------------- #
def test_gemini_error_falls_back_to_ocr_and_logs_reason(monkeypatch, caplog):
    monkeypatch.setattr(vision._client, "generate_vision", _boom)
    monkeypatch.setattr(vision, "run_ocr", lambda _image: "recovered text from ocr")

    with caplog.at_level(logging.WARNING, logger="app.ingestion.extractors.vision"):
        piece = vision.extract_image(_png(), page=5)

    assert piece["extraction_method"] == "ocr"
    assert piece["chunk_type"] == "text"
    assert piece["text"] == "recovered text from ocr"
    assert "OCR fallback" in caplog.text
    assert "500 backend error" in caplog.text


def test_empty_vision_response_falls_back_to_ocr(monkeypatch):
    monkeypatch.setattr(vision._client, "generate_vision", lambda **kw: "   ")
    monkeypatch.setattr(vision, "run_ocr", lambda _image: "ocr text")

    piece = vision.extract_image(_png())

    assert piece["extraction_method"] == "ocr"
    assert piece["chunk_type"] == "text"


def test_rate_limit_pool_exhausted_falls_back_to_ocr(monkeypatch):
    def rate_limited(**_kw):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota")

    monkeypatch.setattr(vision._client, "generate_vision", rate_limited)
    monkeypatch.setattr(vision, "run_ocr", lambda _image: "ocr after 429")

    piece = vision.extract_image(_png())

    assert piece["extraction_method"] == "ocr"
    assert piece["text"] == "ocr after 429"


# --------------------------------------------------------------------------- #
# MAX_VISION_PAGES hard cap
# --------------------------------------------------------------------------- #
def test_batch_over_cap_spends_budget_then_falls_back_to_ocr(monkeypatch):
    """The cap bounds API spend, not how much of the file is read.

    Every page must still come back: the first MAX_VISION_PAGES from vision,
    the overflow from OCR. Failing the batch would discard the file's other
    pages too, which is what D-19 forbids.
    """
    monkeypatch.setattr(settings, "MAX_VISION_PAGES", 2)

    calls = {"n": 0}

    def _vision(**kw):
        calls["n"] += 1
        return "CONTENT_TYPE: figure\nA plain grey box."

    monkeypatch.setattr(vision._client, "generate_vision", _vision)
    monkeypatch.setattr(vision, "run_ocr", lambda image: "ocr over budget")

    # Distinct colours per page: the vision cache is keyed on image bytes, so
    # identical pages would collapse into one call and hide the budget count.
    colours = ("red", "green", "blue", "yellow", "purple")
    items = [
        vision.VisionPage(image_bytes=_png(color=c), page=i)
        for i, c in enumerate(colours)
    ]
    pieces = vision.extract_pages(items)

    assert len(pieces) == 5
    assert [p["extraction_method"] for p in pieces] == [
        "vision", "vision", "ocr", "ocr", "ocr",
    ]
    assert calls["n"] == 2  # budget spent exactly once per allowed page
    assert [p["page"] for p in pieces] == [0, 1, 2, 3, 4]
    assert pieces[4]["text"] == "ocr over budget"


def test_pages_within_cap_never_reach_ocr(monkeypatch):
    monkeypatch.setattr(settings, "MAX_VISION_PAGES", 10)
    monkeypatch.setattr(
        vision._client, "generate_vision", lambda **kw: "CONTENT_TYPE: figure\nA plain grey box."
    )

    def _must_not_run(image):
        raise AssertionError("OCR ran for a page inside the vision budget")

    monkeypatch.setattr(vision, "run_ocr", _must_not_run)

    pieces = vision.extract_pages(
        [vision.VisionPage(image_bytes=_png(), page=i) for i in range(3)]
    )

    assert all(p["extraction_method"] == "vision" for p in pieces)


def test_batch_at_cap_is_allowed(monkeypatch):
    monkeypatch.setattr(settings, "MAX_VISION_PAGES", 3)
    monkeypatch.setattr(
        vision._client, "generate_vision", lambda **kw: "CONTENT_TYPE: figure\nA plain grey box."
    )
    items = [vision.VisionPage(image_bytes=_png(color=c), page=i) for i, c in enumerate(("red", "green", "blue"))]

    pieces = vision.extract_pages(items)

    assert len(pieces) == 3
    assert all(p["extraction_method"] == "vision" for p in pieces)


# --------------------------------------------------------------------------- #
# total failure -> tagged, pipeline-isolable error (D-19)
# --------------------------------------------------------------------------- #
def test_total_failure_raises_vision_extraction_error(monkeypatch):
    monkeypatch.setattr(vision._client, "generate_vision", _boom)
    monkeypatch.setattr(vision, "run_ocr", _ocr_unavailable)

    with pytest.raises(vision.VisionExtractionError):
        vision.extract_image(_png(), page=1)


def test_pipeline_isolates_total_vision_failure_per_file(monkeypatch):
    monkeypatch.setattr(vision._client, "generate_vision", _boom)
    monkeypatch.setattr(vision, "run_ocr", _ocr_unavailable)

    result = ingest_files(
        [("figure.pdf", _pdf_text_then_image()), ("ok.pdf", _pdf_all_text())],
        corpus_scope="persistent",
    )

    assert "figure.pdf" in {f.filename for f in result.failed}
    assert "ok.pdf" in result.succeeded
    assert len(result.chunks) > 0  # the good file still produced chunks


# --------------------------------------------------------------------------- #
# pdf.py page-selection integration
# --------------------------------------------------------------------------- #
def test_low_text_image_page_goes_to_vision_normal_page_does_not(monkeypatch):
    calls = []

    def gen(**kw):
        calls.append(kw)
        return "CONTENT_TYPE: figure\nA full-page grey rectangle."

    monkeypatch.setattr(vision._client, "generate_vision", gen)

    pieces = pdf_extract(_pdf_text_then_image(), "doc.pdf")
    by_page = {p["page"]: p for p in pieces}

    assert by_page[1]["extraction_method"] == "text"
    assert by_page[2]["extraction_method"] == "vision"
    assert by_page[2]["chunk_type"] == "image_caption"
    assert len(calls) == 1  # only the image page was dispatched
