import pymupdf

from app.ingestion.extractors import vision

SCANNED_PAGE_CHAR_THRESHOLD = 50

# DPI for rasterising a page before it goes to the vision pass.
_VISION_RENDER_DPI = 150


def extract(content: bytes, filename: str):
    """Extract text from a PDF, page by page.

    Pages with a usable text layer are extracted directly. Pages with almost no
    text layer are handed to the vision path (vision.py), which resolves each
    to extraction_method="vision" or, on fallback, "ocr" — the old interim
    "scanned" marker (not a valid Chunk.extraction_method) is gone.
    """
    doc = pymupdf.open(stream=content, filetype="pdf")
    pages = []
    vision_queue: list[vision.VisionPage] = []

    for page_number, page in enumerate(doc, start=1):
        text = page.get_text().strip()

        if len(text) >= SCANNED_PAGE_CHAR_THRESHOLD:
            pages.append(
                {"page": page_number, "text": text, "extraction_method": "text"}
            )
            continue

        # Low text layer: send to vision only if a large embedded image says
        # this page is a scan / full-bleed figure; otherwise it's just a sparse
        # page and its (little) text stands as-is.
        if vision.page_needs_vision(text, _image_coverage(page)):
            vision_queue.append(
                vision.VisionPage(
                    image_bytes=_render_page_png(page),
                    page=page_number,
                    mime_type="image/png",
                )
            )
        else:
            pages.append(
                {"page": page_number, "text": text, "extraction_method": "text"}
            )

    doc.close()

    if vision_queue:
        # extract_pages spends MAX_VISION_PAGES as a budget, sending overflow
        # pages to OCR rather than failing. It still raises
        # VisionExtractionError when a page has no usable output from either
        # engine; that propagates for the pipeline to isolate per-file (D-19).
        pages.extend(vision.extract_pages(vision_queue))

    pages.sort(key=lambda piece: piece["page"])
    return pages


def _image_coverage(page: "pymupdf.Page") -> float:
    """Fraction of the page area covered by placed raster images (capped at 1.0)."""
    page_area = abs(page.rect.width * page.rect.height)
    if page_area == 0:
        return 0.0

    covered = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        covered += abs((x1 - x0) * (y1 - y0))

    return min(covered / page_area, 1.0)


def _render_page_png(page: "pymupdf.Page") -> bytes:
    return page.get_pixmap(dpi=_VISION_RENDER_DPI).tobytes("png")
