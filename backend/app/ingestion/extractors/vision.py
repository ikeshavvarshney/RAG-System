"""Gemini vision extraction for figures, with an OCR fallback (INGEST-02, D-07).

For a standalone image or a low-text PDF page that looks like a figure, this
module asks a Gemini vision model for a *structured* transcription — tables as
markdown, charts as type + axes + data points — tags it ``chunk_type`` /
``extraction_method="vision"``, and disk-caches the raw response by image hash.

If the vision call fails (error, rate-limit pool exhausted) or comes back
empty/unusable, it falls back to Tesseract OCR (``extraction_method="ocr"``,
``chunk_type="text"``) and logs why. If OCR is also unavailable the image
surfaces as :class:`VisionExtractionError`, which the pipeline isolates
per-file (D-19) rather than letting it crash the batch.

D-28 / model choice (PROVISIONAL): the model is ``settings.VISION_MODEL``
(currently ``"gemini-3.6-flash"``). The Flash-vs-Pro comparison could not be
completed — the available API key has no Gemini Pro quota (HTTP 429/404 on
every Pro model). Flash alone was evaluated on 8 real corpus figures and did
well on structured table/chart transcription. Revisit when a Pro-capable key
exists; switching models is one line in ``config.py``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.core.gemini_client import GeminiClient
from app.ingestion.extractors.ocr import OCRUnavailable, run_ocr

logger = logging.getLogger(__name__)

_client = GeminiClient()

# --- page-selection heuristic thresholds --------------------------------------
# A PDF page goes to the vision pass only when it has almost no text layer AND a
# raster image covers most of it (i.e. it is a scanned page or a full-bleed
# figure). A page with real body text is left to the normal text extractor even
# if it also contains a chart.
LOW_TEXT_CHAR_LIMIT = 100
LARGE_IMAGE_COVERAGE = 0.50

# Shortest model response we will treat as a usable transcription.
_MIN_USABLE_CHARS = 12

# Key-rotation retry budget for a single vision call. Kept low (2 total
# attempts) on purpose: when the key pool is quota-throttled we want to fail
# fast to the OCR fallback (D-07) rather than burn minutes on backoff. The OCR
# tagging (extraction_method="ocr", chunk_type="text") is unchanged.
_VISION_MAX_RETRIES = 1

_VISION_PROMPT = (
    "You are transcribing a figure from a document for a retrieval system.\n"
    "The FIRST line of your reply must be exactly one of:\n"
    "CONTENT_TYPE: table\n"
    "CONTENT_TYPE: chart\n"
    "CONTENT_TYPE: figure\n"
    "From the next line onward:\n"
    "- table: transcribe it as GitHub-flavored markdown, preserving every row, "
    "column and cell value.\n"
    "- chart: state the chart type, its title, each axis label with units and "
    "visible range, and every data point or series value you can read.\n"
    "- figure: give a one-paragraph factual caption of what is visibly present.\n"
    "Add no interpretation or commentary beyond what is visible."
)

_CONTENT_TYPE_RE = re.compile(
    r"CONTENT_TYPE:\s*(table|chart|figure)\s*\n?", re.IGNORECASE
)
_DECLARED_TO_CHUNK_TYPE = {
    "table": "table",
    "chart": "chart",
    "figure": "image_caption",
}


class VisionExtractionError(Exception):
    """Vision AND OCR both failed for one image.

    Raised so the ingestion pipeline can record a per-file failure (D-19)
    instead of aborting the whole batch.
    """


class VisionPageCapExceeded(VisionExtractionError):
    """A vision batch selected more pages than ``settings.MAX_VISION_PAGES``.

    No longer raised by :func:`extract_pages`, which now spends the cap as a
    budget and sends the overflow to OCR. Kept so a caller that still catches
    it does not break, and so the name stays reserved for a genuinely fatal
    cap should one be reintroduced.
    """

    def __init__(self, selected: int, cap: int):
        self.selected = selected
        self.cap = cap
        super().__init__(
            f"vision batch would dispatch {selected} pages, over the "
            f"MAX_VISION_PAGES={cap} hard cap; refusing to proceed"
        )


class _UnusableVisionResponse(Exception):
    """Internal: model replied but with nothing we can index — triggers OCR."""


@dataclass
class VisionPage:
    """One image queued for the vision pass."""

    image_bytes: bytes
    page: int | None = None
    location: str | None = None
    mime_type: str = "image/png"


# --------------------------------------------------------------------------- #
# Page-selection heuristic (used by pdf.py)
# --------------------------------------------------------------------------- #
def page_needs_vision(text: str, image_coverage: float) -> bool:
    """True when a PDF page has very little text AND a large embedded image."""
    return (
        len(text.strip()) < LOW_TEXT_CHAR_LIMIT
        and image_coverage >= LARGE_IMAGE_COVERAGE
    )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_pages(items: list[VisionPage]) -> list[dict]:
    """Run the vision pass over a batch, spending MAX_VISION_PAGES as a budget.

    The cap bounds API spend on one file, not how much of the file is read.
    The first ``MAX_VISION_PAGES`` pages get vision; the overflow goes straight
    to OCR and is tagged ``extraction_method="ocr"``, so a 200-page scan yields
    200 indexed pages of mixed quality rather than nothing at all.

    This replaces an earlier hard refusal. Failing the batch discarded the
    file's text pages too, which contradicts D-19 (one oversized file must not
    cost the corpus that file) and D-07 (an unavailable vision path degrades to
    OCR rather than dropping content). Budget order is page order: pages are
    queued in document order, so the overflow is the tail of the document.
    """
    cap = settings.MAX_VISION_PAGES
    if len(items) > cap:
        logger.warning(
            "vision batch of %d pages exceeds MAX_VISION_PAGES=%d; "
            "pages beyond the cap fall back to OCR",
            len(items),
            cap,
        )

    pieces: list[dict] = []
    for position, item in enumerate(items):
        if position < cap:
            pieces.append(
                extract_image(
                    item.image_bytes,
                    page=item.page,
                    location=item.location,
                    mime_type=item.mime_type,
                )
            )
        else:
            pieces.append(
                _ocr_fallback(
                    item.image_bytes,
                    item.page,
                    item.location,
                    f"over MAX_VISION_PAGES={cap} budget for this file",
                )
            )

    return pieces


def extract_image(
    image_bytes: bytes,
    *,
    page: int | None = None,
    location: str | None = None,
    mime_type: str = "image/png",
) -> dict:
    """Transcribe one image, falling back to OCR. Returns a piece dict:
    ``{page, location, text, extraction_method, chunk_type}``.

    Raises :class:`VisionExtractionError` only when vision *and* OCR both fail.
    """
    model_id = settings.VISION_MODEL

    cache_key = _cache_key(image_bytes, model_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        try:
            chunk_type, body = _parse_vision_response(cached)
            return _piece(body, "vision", chunk_type, page, location)
        except _UnusableVisionResponse:
            logger.warning("stale/unusable vision cache entry for page=%s; recomputing", page)

    raw: str | None = None
    fail_reason: str | None = None
    try:
        raw = _client.generate_vision(
            stage="vision_extraction",
            model=model_id,
            prompt=_VISION_PROMPT,
            image_bytes=image_bytes,
            mime_type=mime_type,
            max_retries=_VISION_MAX_RETRIES,
        )
    except Exception as exc:  # includes rate-limit pool exhausted (re-raised)
        fail_reason = f"vision call failed: {type(exc).__name__}: {exc}"

    if raw is not None:
        try:
            chunk_type, body = _parse_vision_response(raw)
            _cache_put(cache_key, raw)  # cache only clean successes
            return _piece(body, "vision", chunk_type, page, location)
        except _UnusableVisionResponse as exc:
            fail_reason = f"unusable vision response: {exc}"

    logger.warning("vision -> OCR fallback (page=%s): %s", page, fail_reason)
    return _ocr_fallback(image_bytes, page, location, fail_reason)


def _ocr_fallback(
    image_bytes: bytes,
    page: int | None,
    location: str | None,
    vision_reason: str | None,
) -> dict:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        ocr_text = run_ocr(image)
    except OCRUnavailable as exc:
        raise VisionExtractionError(
            f"vision and OCR both unavailable (page={page}): {vision_reason}; "
            f"OCR: {exc}"
        ) from exc
    except Exception as exc:
        raise VisionExtractionError(
            f"vision failed and OCR errored (page={page}): {vision_reason}; "
            f"OCR: {type(exc).__name__}: {exc}"
        ) from exc

    return _piece(ocr_text, "ocr", "text", page, location)


# --------------------------------------------------------------------------- #
# Response parsing / classification
# --------------------------------------------------------------------------- #
def _parse_vision_response(raw: str) -> tuple[str, str]:
    """Return ``(chunk_type, body_text)`` or raise :class:`_UnusableVisionResponse`."""
    if raw is None or not raw.strip():
        raise _UnusableVisionResponse("empty response")

    match = _CONTENT_TYPE_RE.search(raw[:120])
    if match:
        declared = match.group(1).lower()
        body = raw[match.end() :].strip()
        if len(body) < _MIN_USABLE_CHARS:
            raise _UnusableVisionResponse("CONTENT_TYPE tag with no transcription")
        return _DECLARED_TO_CHUNK_TYPE[declared], body

    body = raw.strip()
    if len(body) < _MIN_USABLE_CHARS:
        raise _UnusableVisionResponse("response too short to index")
    return _heuristic_chunk_type(body), body


def _heuristic_chunk_type(text: str) -> str:
    """Fallback classifier when the model omits the CONTENT_TYPE tag."""
    pipe_rows = sum(1 for line in text.splitlines() if line.count("|") >= 2)
    if pipe_rows >= 2:
        return "table"

    lowered = text.lower()
    chart_markers = (
        "chart type",
        "x-axis",
        "y-axis",
        "x axis",
        "y axis",
        "axis label",
        "data point",
        "bar chart",
        "line chart",
        "scatter plot",
        "pie chart",
    )
    if any(marker in lowered for marker in chart_markers):
        return "chart"
    return "image_caption"


def _piece(
    text: str,
    extraction_method: str,
    chunk_type: str,
    page: int | None,
    location: str | None,
) -> dict:
    return {
        "page": page,
        "location": location,
        "text": text,
        "extraction_method": extraction_method,
        "chunk_type": chunk_type,
    }


# --------------------------------------------------------------------------- #
# Disk cache — mirrors embedder.py: sha256-keyed JSON, atomic .tmp + replace
# --------------------------------------------------------------------------- #
def _cache_dir() -> Path:
    path = Path(settings.VISION_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(image_bytes: bytes, model_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(model_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(image_bytes)
    return digest.hexdigest()


def _cache_get(key: str) -> str | None:
    path = _cache_dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    except (json.JSONDecodeError, OSError, KeyError):
        logger.warning("ignoring unreadable vision cache entry %s", path.name)
        return None


def _cache_put(key: str, raw_response: str) -> None:
    directory = _cache_dir()
    path = directory / f"{key}.json"
    tmp = directory / f"{key}.json.tmp"
    try:
        tmp.write_text(
            json.dumps({"model": settings.VISION_MODEL, "response": raw_response}),
            encoding="utf-8",
        )
        tmp.replace(path)  # atomic on the same filesystem
    except OSError:
        logger.warning("could not persist vision cache entry %s", path.name)
