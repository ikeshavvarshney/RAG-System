import io

from PIL import Image

from app.ingestion.extractors.ocr import run_ocr


def extract(content: bytes, filename: str):
    """Load an image and OCR it into a single text chunk.

    Decorative images with no text yield zero chunks rather than a chunk
    of whitespace.
    """
    image = Image.open(io.BytesIO(content))
    text = run_ocr(image)

    if not text:
        return []

    return [{
        "page": None,
        "location": None,
        "text": text,
        "extraction_method": "ocr",
        "chunk_type": "text",
    }]