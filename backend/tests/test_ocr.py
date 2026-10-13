import pytest
from PIL import Image, ImageDraw

from app.core.config import settings
from app.ingestion.extractors import ocr as ocr_module
from app.ingestion.extractors.ocr import OCRUnavailable, run_ocr


def _text_image(text: str = "INVOICE 2024") -> Image.Image:
    image = Image.new("RGB", (400, 100), color="white")
    ImageDraw.Draw(image).text((10, 30), text, fill="black")
    return image


# --------------------------------------------------------------------------- #
# Tesseract
# --------------------------------------------------------------------------- #

def test_tesseract_output_is_stripped(monkeypatch):
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda image: "  from tesseract \n"
    )

    assert run_ocr(_text_image()) == "from tesseract"


def test_missing_tesseract_raises_ocr_unavailable(monkeypatch):
    def _no_tesseract(image):
        raise ocr_module.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _no_tesseract)

    with pytest.raises(OCRUnavailable) as excinfo:
        run_ocr(_text_image())

    assert "Tesseract unavailable" in str(excinfo.value)
    assert "TESSERACT_CMD" in str(excinfo.value)


def test_unexecutable_tesseract_is_ocr_unavailable(monkeypatch):
    """A binary the OS refuses to run must degrade like a missing one."""

    def _denied(image):
        raise PermissionError(13, "Access is denied", None, 5)

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _denied)

    with pytest.raises(OCRUnavailable) as excinfo:
        run_ocr(_text_image())

    assert "PermissionError" in str(excinfo.value)
    assert "TESSERACT_CMD" in str(excinfo.value)


def test_real_tesseract_reads_a_rendered_page():
    """End to end against the configured binary, on a page rendered large enough to recognise."""
    try:
        ocr_module.pytesseract.get_tesseract_version()
    except (ocr_module.pytesseract.TesseractNotFoundError, OSError):
        pytest.skip(f"no Tesseract binary at TESSERACT_CMD={settings.TESSERACT_CMD}")

    image = _text_image("INVOICE 2024").resize((1600, 400), Image.LANCZOS)

    assert "2024" in run_ocr(image)


# --------------------------------------------------------------------------- #
# Result cache
# --------------------------------------------------------------------------- #
def test_second_ocr_of_the_same_image_is_served_from_the_cache(monkeypatch):
    calls = []

    def _tesseract(image):
        calls.append(1)
        return "first result"

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _tesseract)

    assert run_ocr(_text_image()) == "first result"
    assert run_ocr(_text_image()) == "first result"
    assert len(calls) == 1


def test_a_different_image_misses_the_cache(monkeypatch):
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", lambda image: "page one")
    assert run_ocr(_text_image("PAGE ONE")) == "page one"

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", lambda image: "page two")
    assert run_ocr(_text_image("PAGE TWO")) == "page two"


def test_a_failed_ocr_is_not_cached(monkeypatch):
    def _no_tesseract(image):
        raise ocr_module.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _no_tesseract)
    with pytest.raises(OCRUnavailable):
        run_ocr(_text_image())

    # Tesseract is back: the page must be recognised, not served an empty entry.
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", lambda image: "recovered")
    assert run_ocr(_text_image()) == "recovered"


def test_an_entry_written_by_another_engine_is_not_served(monkeypatch, tmp_path):
    """Cache files left by an earlier OCR engine hash differently, so they are ignored."""
    image = _text_image()
    stale = ocr_module._cache_dir() / "not-the-tesseract-key.json"
    stale.write_text('{"engine": "other", "text": "stale text"}', encoding="utf-8")
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", lambda image: "fresh text")

    assert run_ocr(image) == "fresh text"
