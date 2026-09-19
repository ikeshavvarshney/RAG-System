"""OCR entry point: PaddleOCR primary, Tesseract fallback (D-27)."""

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
pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


class OCRUnavailable(Exception):
    """Raised when no OCR engine at all could be reached."""


def run_ocr(image: Image.Image) -> str:
    """Run OCR on a PIL Image and return extracted text."""
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
    """Run the Tesseract fallback."""
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
