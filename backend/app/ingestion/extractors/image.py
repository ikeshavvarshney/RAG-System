import logging

from app.ingestion.extractors import vision

logger = logging.getLogger(__name__)


def _mime_for(content: bytes) -> str:
    return "image/jpeg" if content[:3] == b"\xff\xd8\xff" else "image/png"


def extract(content: bytes, filename: str, vision_units: set[int] | None = None):
    """Standalone images get the vision pass (INGEST-02) unless the global plan left this one out."""
    if vision_units is not None and 1 not in vision_units:
        logger.warning("%s: image left out of the vision plan; not extracted", filename)
        return []
    piece = vision.extract_image(
        content, page=None, location=None, mime_type=_mime_for(content)
    )
    if not piece["text"].strip():
        return []
    return [piece]
