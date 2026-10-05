"""OCR entry point: PaddleOCR primary, Tesseract fallback (D-27)."""

import hashlib
import json
import logging
from pathlib import Path

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
    """Run OCR on a PIL Image and return extracted text, reusing a cached result for the same image and settings."""
    key = _cache_key(image)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    text, engine = _run_engines(image)
    # A result from a fallback engine is not cached: it would be served later as the configured engine's output.
    if engine == settings.OCR_ENGINE:
        _cache_put(key, text, engine)
    return text


def _run_engines(image: Image.Image) -> tuple[str, str]:
    """Try the configured engine, then the rest of the cascade. Returns the text and the engine that produced it."""
    paddle_error: str | None = None

    if settings.OCR_ENGINE == "paddle-structure":
        try:
            return run_paddle_structure(image), "paddle-structure"
        except PaddleUnavailable as exc:
            paddle_error = str(exc)
            logger.warning(
                "PP-StructureV3 unavailable, falling back to plain PaddleOCR: %s", exc
            )

    if settings.OCR_ENGINE in ("paddle", "paddle-structure"):
        try:
            return run_paddle_ocr(image), "paddle"
        except PaddleUnavailable as exc:
            paddle_error = f"{paddle_error}; {exc}" if paddle_error else str(exc)
            logger.warning("PaddleOCR unavailable, falling back to Tesseract: %s", exc)

    return run_tesseract_ocr(image, paddle_error=paddle_error), "tesseract"


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


# --------------------------------------------------------------------------- #
# Disk cache: OCR runs for minutes per page on CPU, and its output depends only on the image and these settings.
# --------------------------------------------------------------------------- #
def _cache_dir() -> Path:
    path = Path(settings.OCR_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(image: Image.Image) -> str:
    digest = hashlib.sha256()
    config = (
        settings.OCR_ENGINE,
        settings.OCR_LANG,
        settings.OCR_MIN_CONFIDENCE,
        settings.OCR_TEXTLINE_ORIENTATION,
        image.mode,
        image.size,
    )
    digest.update(repr(config).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(image.tobytes())
    return digest.hexdigest()


def _cache_get(key: str) -> str | None:
    path = _cache_dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["text"]
    except (json.JSONDecodeError, OSError, KeyError):
        logger.warning("ignoring unreadable OCR cache entry %s", path.name)
        return None


def _cache_put(key: str, text: str, engine: str) -> None:
    directory = _cache_dir()
    path = directory / f"{key}.json"
    tmp = directory / f"{key}.json.tmp"
    try:
        tmp.write_text(json.dumps({"engine": engine, "text": text}), encoding="utf-8")
        tmp.replace(path)  # atomic on the same filesystem
    except OSError:
        logger.warning("could not persist OCR cache entry %s", path.name)
