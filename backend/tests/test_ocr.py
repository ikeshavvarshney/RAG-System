import pytest
from PIL import Image, ImageDraw

from app.core.config import settings
from app.ingestion.extractors import ocr as ocr_module
from app.ingestion.extractors import paddle_ocr as paddle_module
from app.ingestion.extractors.ocr import OCRUnavailable, run_ocr
from app.ingestion.extractors.paddle_ocr import (
    PaddleUnavailable,
    _collect_text,
    _html_to_markdown,
    _table_to_markdown,
    get_engine,
    get_structure_engine,
    reset_engine,
    run_paddle_ocr,
    run_paddle_structure,
)


def _text_image(text: str = "INVOICE 2024") -> Image.Image:
    image = Image.new("RGB", (400, 100), color="white")
    ImageDraw.Draw(image).text((10, 30), text, fill="black")
    return image


@pytest.fixture(autouse=True)
def _reset_paddle_engine():
    reset_engine()
    yield
    reset_engine()


# --------------------------------------------------------------------------- #
# Engine dispatch
# --------------------------------------------------------------------------- #

def test_paddle_engine_is_used_when_selected(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle")
    monkeypatch.setattr(ocr_module, "run_paddle_ocr", lambda image: "from paddle")

    assert run_ocr(_text_image()) == "from paddle"


def test_tesseract_engine_skips_paddle_entirely(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "tesseract")

    def _must_not_run(image):
        raise AssertionError("paddle was called despite OCR_ENGINE=tesseract")

    monkeypatch.setattr(ocr_module, "run_paddle_ocr", _must_not_run)
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda image: "  from tesseract \n"
    )

    assert run_ocr(_text_image()) == "from tesseract"


def test_unavailable_paddle_falls_back_to_tesseract(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle")

    def _boom(image):
        raise PaddleUnavailable("engine init failed: ImportError: no paddle")

    monkeypatch.setattr(ocr_module, "run_paddle_ocr", _boom)
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda image: "fallback text"
    )

    assert run_ocr(_text_image()) == "fallback text"


def test_both_engines_gone_raises_ocr_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle")

    def _boom(image):
        raise PaddleUnavailable("engine init failed: ImportError: no paddle")

    def _no_tesseract(image):
        raise ocr_module.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr_module, "run_paddle_ocr", _boom)
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _no_tesseract)

    with pytest.raises(OCRUnavailable) as excinfo:
        run_ocr(_text_image())

    # The message must name both engines, otherwise a missing paddle looks
    # like a missing Tesseract install to whoever reads the ingestion log.
    assert "Tesseract unavailable" in str(excinfo.value)
    assert "PaddleOCR also unavailable" in str(excinfo.value)


def test_unexecutable_tesseract_is_ocr_unavailable(monkeypatch):
    """A binary the OS refuses to run must degrade like a missing one.

    The vendored Windows copy can raise PermissionError (WinError 5). If that
    escapes as itself, one broken install fails every page instead of being
    reported once as an unavailable engine.
    """
    monkeypatch.setattr(settings, "OCR_ENGINE", "tesseract")

    def _denied(image):
        raise PermissionError(13, "Access is denied", None, 5)

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_string", _denied)

    with pytest.raises(OCRUnavailable) as excinfo:
        run_ocr(_text_image())

    assert "PermissionError" in str(excinfo.value)
    assert "TESSERACT_CMD" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Paddle wrapper
# --------------------------------------------------------------------------- #

class _FakeEngine:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def predict(self, array):
        self.calls.append(array)
        return self.results


def test_run_paddle_ocr_joins_lines_in_order(monkeypatch):
    engine = _FakeEngine([{"rec_texts": ["Year 2024", "Total 1500"], "rec_scores": [0.99, 0.97]}])
    monkeypatch.setattr(paddle_module, "get_engine", lambda: engine)

    assert run_paddle_ocr(_text_image()) == "Year 2024\nTotal 1500"


def test_run_paddle_ocr_passes_rgb_array(monkeypatch):
    engine = _FakeEngine([{"rec_texts": ["x"], "rec_scores": [0.99]}])
    monkeypatch.setattr(paddle_module, "get_engine", lambda: engine)

    run_paddle_ocr(Image.new("L", (20, 10), color=255))

    assert engine.calls[0].shape == (10, 20, 3)


def test_predict_failure_becomes_paddle_unavailable(monkeypatch):
    class _Broken:
        def predict(self, array):
            raise RuntimeError("ConvertPirAttribute2RuntimeAttribute not support")

    monkeypatch.setattr(paddle_module, "get_engine", lambda: _Broken())

    with pytest.raises(PaddleUnavailable) as excinfo:
        run_paddle_ocr(_text_image())

    assert "predict failed" in str(excinfo.value)


def test_low_confidence_and_blank_detections_are_dropped():
    results = [
        {
            "rec_texts": ["real line", "n0is3", "   ", "second line"],
            "rec_scores": [0.95, 0.10, 0.99, 0.80],
        }
    ]

    assert _collect_text(results) == "real line\nsecond line"


def test_collect_text_tolerates_empty_and_keyless_results():
    assert _collect_text([]) == ""
    assert _collect_text(None) == ""
    assert _collect_text([{}]) == ""


def test_missing_scores_are_treated_as_confident():
    assert _collect_text([{"rec_texts": ["kept"]}]) == "kept"


# --------------------------------------------------------------------------- #
# Engine lifecycle
# --------------------------------------------------------------------------- #

def test_engine_is_built_once_and_reused(monkeypatch):
    builds = []

    def _build():
        builds.append(1)
        return _FakeEngine([])

    monkeypatch.setattr(paddle_module, "_build_engine", _build)

    assert get_engine() is get_engine()
    assert len(builds) == 1


def test_failed_build_is_remembered_not_retried(monkeypatch):
    attempts = []

    def _build():
        attempts.append(1)
        raise ImportError("No module named 'paddleocr'")

    monkeypatch.setattr(paddle_module, "_build_engine", _build)

    for _ in range(3):
        with pytest.raises(PaddleUnavailable):
            get_engine()

    # One failed import, not one per page: the fallback must be cheap.
    assert len(attempts) == 1


def test_reset_engine_allows_a_rebuild(monkeypatch):
    builds = []
    monkeypatch.setattr(
        paddle_module, "_build_engine", lambda: (builds.append(1), _FakeEngine([]))[1]
    )

    get_engine()
    reset_engine()
    get_engine()

    assert len(builds) == 2


# --------------------------------------------------------------------------- #
# PP-StructureV3 (D-27 table structure)
# --------------------------------------------------------------------------- #

class _FakeStructureResult:
    def __init__(self, markdown):
        self.markdown = markdown


class _FakeStructureEngine:
    def __init__(self, markdowns):
        self.markdowns = markdowns

    def predict(self, array):
        return [_FakeStructureResult(m) for m in self.markdowns]


def test_structure_engine_is_used_when_selected(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle-structure")
    monkeypatch.setattr(ocr_module, "run_paddle_structure", lambda image: "| a | b |")

    assert run_ocr(_text_image()) == "| a | b |"


def test_structure_falls_back_to_plain_paddle(monkeypatch):
    """Losing table structure beats losing the page."""
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle-structure")

    def _boom(image):
        raise PaddleUnavailable("structure engine init failed: DependencyError")

    monkeypatch.setattr(ocr_module, "run_paddle_structure", _boom)
    monkeypatch.setattr(ocr_module, "run_paddle_ocr", lambda image: "flat text")

    assert run_ocr(_text_image()) == "flat text"


def test_structure_falls_all_the_way_to_tesseract(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle-structure")

    def _boom(image):
        raise PaddleUnavailable("gone")

    monkeypatch.setattr(ocr_module, "run_paddle_structure", _boom)
    monkeypatch.setattr(ocr_module, "run_paddle_ocr", _boom)
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda image: "last resort"
    )

    assert run_ocr(_text_image()) == "last resort"


def test_plain_paddle_never_invokes_structure(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENGINE", "paddle")

    def _must_not_run(image):
        raise AssertionError("structure ran despite OCR_ENGINE=paddle")

    monkeypatch.setattr(ocr_module, "run_paddle_structure", _must_not_run)
    monkeypatch.setattr(ocr_module, "run_paddle_ocr", lambda image: "plain")

    assert run_ocr(_text_image()) == "plain"


def test_structure_predict_failure_becomes_paddle_unavailable(monkeypatch):
    class _Broken:
        def predict(self, array):
            raise RuntimeError("layout model exploded")

    monkeypatch.setattr(paddle_module, "get_structure_engine", lambda: _Broken())

    with pytest.raises(PaddleUnavailable) as excinfo:
        run_paddle_structure(_text_image())

    assert "structure predict failed" in str(excinfo.value)


def test_structure_result_dict_markdown_is_unwrapped(monkeypatch):
    engine = _FakeStructureEngine([{"markdown_texts": "# Heading\n\nBody text."}])
    monkeypatch.setattr(paddle_module, "get_structure_engine", lambda: engine)

    assert run_paddle_structure(_text_image()) == "# Heading\n\nBody text."


def test_structure_engine_is_separate_from_plain_engine(monkeypatch):
    """A missing paddlex[ocr] extra must not poison plain recognition."""
    monkeypatch.setattr(paddle_module, "_build_engine", lambda: _FakeEngine([]))

    def _no_extra():
        raise ImportError("PP-StructureV3 requires additional dependencies")

    monkeypatch.setattr(paddle_module, "_build_structure_engine", _no_extra)

    with pytest.raises(PaddleUnavailable):
        get_structure_engine()

    assert get_engine() is not None


# --------------------------------------------------------------------------- #
# HTML table -> markdown, so the splitter tags it chunk_type="table"
# --------------------------------------------------------------------------- #

def test_html_table_becomes_a_markdown_pipe_table():
    html = (
        "<table><tr><td>Year</td><td>Revenue</td></tr>"
        "<tr><td>2023</td><td>1000</td></tr></table>"
    )

    assert _table_to_markdown(html).strip() == (
        "| Year | Revenue |\n| --- | --- |\n| 2023 | 1000 |"
    )


def test_ragged_rows_are_padded_to_the_widest_row():
    html = "<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>1</td></tr></table>"

    assert _table_to_markdown(html).strip().splitlines()[-1] == "| 1 |  |  |"


def test_pipes_inside_cells_are_escaped():
    """An unescaped pipe would split one cell into two columns."""
    html = "<table><tr><td>a|b</td></tr><tr><td>c</td></tr></table>"

    assert "a\\|b" in _table_to_markdown(html)


def test_empty_table_produces_nothing():
    assert _table_to_markdown("<table><tr><td></td></tr></table>") == ""


def test_layout_wrapper_tags_are_stripped():
    """Angle brackets must not reach an embedding as literal text."""
    wrapped = '<div style="text-align: center;">Figure 1. Coursetaking</div>'

    assert _html_to_markdown(wrapped) == "Figure 1. Coursetaking"


def test_markdown_without_html_survives_untouched():
    text = "# Heading\n\nA paragraph with no markup at all."

    assert _html_to_markdown(text) == text
