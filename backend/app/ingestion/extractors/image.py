from app.ingestion.extractors import vision


def _mime_for(content: bytes) -> str:
    return "image/jpeg" if content[:3] == b"\xff\xd8\xff" else "image/png"


def extract(content: bytes, filename: str):
    """Standalone images always get the vision pass (INGEST-02).

    ``vision.extract_image`` performs the OCR fallback internally and only
    raises ``VisionExtractionError`` when vision *and* OCR both fail; that
    propagates for the pipeline to isolate per-file (D-19).

    An image that yields no text at all (blank/decorative) produces zero
    chunks rather than a chunk of whitespace.
    """
    piece = vision.extract_image(
        content, page=None, location=None, mime_type=_mime_for(content)
    )
    if not piece["text"].strip():
        return []
    return [piece]
