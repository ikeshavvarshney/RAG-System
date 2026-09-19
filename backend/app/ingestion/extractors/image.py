from app.ingestion.extractors import vision


def _mime_for(content: bytes) -> str:
    return "image/jpeg" if content[:3] == b"\xff\xd8\xff" else "image/png"


def extract(content: bytes, filename: str):
    """Standalone images always get the vision pass (INGEST-02)."""
    piece = vision.extract_image(
        content, page=None, location=None, mime_type=_mime_for(content)
    )
    if not piece["text"].strip():
        return []
    return [piece]
