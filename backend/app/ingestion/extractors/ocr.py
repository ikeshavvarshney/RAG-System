"""OCR entry point: Tesseract (D-27)."""

import hashlib
import json
import logging
from pathlib import Path

import pytesseract
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

# Explicit path, sourced from settings (override TESSERACT_CMD in .env).
pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

_ENGINE = "tesseract"


class OCRUnavailable(Exception):
    """Raised when the OCR engine could not be reached."""


def run_ocr(image: Image.Image) -> str:
    """Run OCR on a PIL Image and return extracted text, reusing a cached result for the same image."""
    key = _cache_key(image)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    text = run_tesseract_ocr(image)
    _cache_put(key, text)
    return text


def run_tesseract_ocr(image: Image.Image) -> str:
    """Run Tesseract on the image."""
    try:
        text = pytesseract.image_to_string(image)
    except (pytesseract.TesseractNotFoundError, OSError) as exc:
        raise OCRUnavailable(
            f"Tesseract unavailable ({type(exc).__name__}: {exc}). Check the "
            f"binary at TESSERACT_CMD={settings.TESSERACT_CMD}"
        ) from exc

    return text.strip()


# --------------------------------------------------------------------------- #
# Disk cache: OCR output depends only on the image, so a repeated ingestion run does not recognise a page twice.
# --------------------------------------------------------------------------- #
def _cache_dir() -> Path:
    path = Path(settings.OCR_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(image: Image.Image) -> str:
    digest = hashlib.sha256()
    # The engine name is part of the key so entries written by an earlier engine are never served.
    config = (_ENGINE, image.mode, image.size)
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


def _cache_put(key: str, text: str) -> None:
    directory = _cache_dir()
    path = directory / f"{key}.json"
    tmp = directory / f"{key}.json.tmp"
    try:
        tmp.write_text(json.dumps({"engine": _ENGINE, "text": text}), encoding="utf-8")
        tmp.replace(path)  # atomic on the same filesystem
    except OSError:
        logger.warning("could not persist OCR cache entry %s", path.name)
