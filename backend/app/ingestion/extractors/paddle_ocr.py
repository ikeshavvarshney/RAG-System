"""PaddleOCR text recognition, the primary OCR engine (D-27)."""

import logging
import re
import threading

import numpy as np
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)


class PaddleUnavailable(Exception):
    """Raised when the PaddleOCR engine cannot be built or cannot run."""


_engine = None
_engine_lock = threading.Lock()
_init_error: str | None = None

_structure_engine = None
_structure_lock = threading.Lock()
_structure_init_error: str | None = None


def _build_engine():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang=settings.OCR_LANG,
        # oneDNN's CPU kernels raise "ConvertPirAttribute2RuntimeAttribute
        # not support" on this paddle build, so the plain CPU backend is used.
        enable_mkldnn=False,
        # Document-level preprocessing costs seconds per image and the callers (PyMuPDF page
        # renders, embedded figures) already hand us upright images, so orientation and dewarping
        # stay off.
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=settings.OCR_TEXTLINE_ORIENTATION,
    )


def get_engine():
    """Return the shared PaddleOCR engine, building it on first use."""
    global _engine, _init_error

    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
            return _engine
        if _init_error is not None:
            raise PaddleUnavailable(_init_error)
        try:
            _engine = _build_engine()
        except Exception as exc:
            _init_error = f"engine init failed: {type(exc).__name__}: {exc}"
            raise PaddleUnavailable(_init_error) from exc

    logger.info("PaddleOCR engine ready (lang=%s)", settings.OCR_LANG)
    return _engine


def reset_engine() -> None:
    """Drop the cached engines and any remembered init failure (tests)."""
    global _engine, _init_error, _structure_engine, _structure_init_error
    with _engine_lock:
        _engine = None
        _init_error = None
    with _structure_lock:
        _structure_engine = None
        _structure_init_error = None


def run_paddle_ocr(image: Image.Image) -> str:
    """Recognise text in a PIL Image and return it in reading order."""
    engine = get_engine()
    array = np.array(image.convert("RGB"))

    try:
        results = engine.predict(array)
    except Exception as exc:
        raise PaddleUnavailable(
            f"predict failed: {type(exc).__name__}: {exc}"
        ) from exc

    return _collect_text(results)


def _collect_text(results) -> str:
    """Flatten PaddleOCR results into one newline-joined string."""
    lines: list[str] = []

    for result in results or []:
        texts = result["rec_texts"] if "rec_texts" in result else []
        scores = result["rec_scores"] if "rec_scores" in result else []
        for position, text in enumerate(texts):
            score = scores[position] if position < len(scores) else 1.0
            if score < settings.OCR_MIN_CONFIDENCE:
                continue
            stripped = text.strip()
            if stripped:
                lines.append(stripped)

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# PP-StructureV3: layout analysis + table structure recognition (D-27)
# --------------------------------------------------------------------------- #
# Subpipelines that cost a model load and inference pass each but produce
# nothing this corpus needs. Seal and formula recognition are for stamped and
# mathematical documents; chart recognition duplicates what the Gemini vision
# path already does better. Turning them off is most of the difference between
# ~430s and ~75s per page on CPU.
_STRUCTURE_OPTIONS = dict(
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    use_seal_recognition=False,
    use_formula_recognition=False,
    use_chart_recognition=False,
    use_table_recognition=True,
    # Mobile det/rec rather than the server default.
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
)

_TABLE_RE = re.compile(r"<table[\s>].*?</table>", re.IGNORECASE | re.DOTALL)


def _build_structure_engine():
    from paddleocr import PPStructureV3

    return PPStructureV3(**_STRUCTURE_OPTIONS)


def get_structure_engine():
    """Return the shared PP-StructureV3 pipeline, building it on first use."""
    global _structure_engine, _structure_init_error

    if _structure_engine is not None:
        return _structure_engine

    with _structure_lock:
        if _structure_engine is not None:
            return _structure_engine
        if _structure_init_error is not None:
            raise PaddleUnavailable(_structure_init_error)
        try:
            _structure_engine = _build_structure_engine()
        except Exception as exc:
            _structure_init_error = (
                f"structure engine init failed: {type(exc).__name__}: {exc}"
            )
            raise PaddleUnavailable(_structure_init_error) from exc

    logger.info("PP-StructureV3 pipeline ready")
    return _structure_engine


def run_paddle_structure(image: Image.Image) -> str:
    """Recognise one page with layout and table structure, returning markdown."""
    engine = get_structure_engine()
    array = np.array(image.convert("RGB"))

    try:
        results = list(engine.predict(array))
    except Exception as exc:
        raise PaddleUnavailable(
            f"structure predict failed: {type(exc).__name__}: {exc}"
        ) from exc

    parts = []
    for result in results:
        markdown = getattr(result, "markdown", None)
        if isinstance(markdown, dict):
            markdown = markdown.get("markdown_texts", "")
        if markdown:
            parts.append(_html_to_markdown(str(markdown)))

    return "\n\n".join(part for part in parts if part).strip()


def _html_to_markdown(text: str) -> str:
    """Rewrite HTML tables as markdown and drop paddle's layout wrapper tags."""
    text = _TABLE_RE.sub(lambda match: _table_to_markdown(match.group(0)), text)

    from bs4 import BeautifulSoup

    # Only strip tags if any survived the table rewrite; parsing plain markdown
    # through an HTML parser is wasted work on the common path.
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text()

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _table_to_markdown(html: str) -> str:
    """Convert one HTML table to a markdown pipe table."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows: list[list[str]] = []

    for row in soup.find_all("tr"):
        cells = [
            # Pipes inside a cell would otherwise split it into two columns.
            cell.get_text(" ", strip=True).replace("|", r"\|")
            for cell in row.find_all(["td", "th"])
        ]
        if any(cell for cell in cells):
            rows.append(cells)

    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    header, *body = padded
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)

    return "\n" + "\n".join(lines) + "\n"
