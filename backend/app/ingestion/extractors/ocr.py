"""OCR entry point: PaddleOCR primary, Tesseract fallback (D-27).

``run_ocr`` is the seam the rest of ingestion sees. Which engine served a
page is deliberately not exposed: chunks are tagged ``extraction_method="ocr"``
either way, so a machine missing paddle produces the same chunk shape as one
that has it, only less accurately.
"""

import logging

import pytesseract
from PIL import Image

from app.core.config import settings
from app.ingestion.extractors.paddle_ocr import (
    PaddleUnavailable,
    run_paddle_ocr,
    run_paddle_structure,
)

logger = logging.getLogger(__name__)

# Explicit path, sourced from settings (override TESSERACT_CMD in .env).
# Keeps the wrapper working regardless of system PATH and lets each machine
# point at its own install without editing code. Default is the vendored
# copy at <repo>/vendor/tesseract/ (gitignored, see README).
pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


class OCRUnavailable(Exception):
    """Raised when no OCR engine at all could be reached."""


def run_ocr(image: Image.Image) -> str:
    """Run OCR on a PIL Image and return extracted text.

    Engines are tried in descending order of capability from whichever
    OCR_ENGINE names, each falling through to the next when it is unavailable
    (paddle not installed, weights undownloadable, inference error). Per D-27
    an unavailable primary engine must not block ingestion, so a page is only
    lost when every engine is gone, which raises :class:`OCRUnavailable`.

    Falling from structure to plain recognition loses table structure but
    keeps the text, which is the right trade for one page of one document.
    The log records it so a run whose tables silently flattened is diagnosable
    afterwards.
    """
    paddle_error: str | None = None

    if settings.OCR_ENGINE == "paddle-structure":
        try:
            return run_paddle_structure(image)
        except PaddleUnavailable as exc:
            paddle_error = str(exc)
            logger.warning(
                "PP-StructureV3 unavailable, falling back to plain PaddleOCR: %s", exc
            )

    if settings.OCR_ENGINE in ("paddle", "paddle-structure"):
        try:
            return run_paddle_ocr(image)
        except PaddleUnavailable as exc:
            paddle_error = f"{paddle_error}; {exc}" if paddle_error else str(exc)
            logger.warning("PaddleOCR unavailable, falling back to Tesseract: %s", exc)

    return run_tesseract_ocr(image, paddle_error=paddle_error)


def run_tesseract_ocr(image: Image.Image, paddle_error: str | None = None) -> str:
    """Run the Tesseract fallback.

    Every way the binary can be unreachable maps to :class:`OCRUnavailable`,
    not just a missing file: a vendored copy the OS refuses to execute
    (PermissionError) or a broken install (OSError) is the same situation for
    the caller, and letting those escape as themselves turns an engine problem
    into a failed page. When paddle was tried first its reason is folded into
    the message, so an ingestion log never blames Tesseract alone for what was
    really two unavailable engines.
    """
    try:
        text = pytesseract.image_to_string(image)
    except (pytesseract.TesseractNotFoundError, OSError) as exc:
        detail = (
            f"Tesseract unavailable ({type(exc).__name__}: {exc}). Check the "
            f"binary at TESSERACT_CMD={settings.TESSERACT_CMD}"
        )
        if paddle_error is not None:
            detail = f"{detail}; PaddleOCR also unavailable: {paddle_error}"
        raise OCRUnavailable(detail) from exc

    return text.strip()
