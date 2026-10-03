import pymupdf

from app.core.config import settings
from app.ingestion import vision_select
from app.ingestion.extractors import vision

# DPI for rasterising a page before it goes to the vision pass or to OCR.
_RENDER_DPI = 150


def extract(content: bytes, filename: str, vision_units: set[int] | None = None):
    """Extract text from a PDF, page by page.

    ``vision_units`` is the set of page numbers the global plan sent to vision. Called without one, the file plans
    for itself. A scanned page (no text layer, page-sized image) goes straight to OCR (D-17) and never counts
    against the vision budget.
    """
    doc = pymupdf.open(stream=content, filetype="pdf")
    try:
        if vision_units is None:
            analysis = vision_select.analyze_pdf(doc, filename)
            vision_units = vision_select.select(
                analysis.candidates, cap=settings.MAX_VISION_PAGES, strict=False
            ).units_for(filename)

        pages = []
        vision_queue: list[vision.VisionPage] = []
        for page_number, page in enumerate(doc, start=1):
            text = page.get_text().strip()

            if len(text) < vision_select.SCANNED_PAGE_CHAR_THRESHOLD and (
                vision_select.image_coverage(page) >= vision_select.LARGE_IMAGE_COVERAGE
            ):
                pages.append(vision.ocr_only(_render_page_png(page), page=page_number))
                continue

            wants_vision = page_number in vision_units
            if wants_vision:
                vision_queue.append(
                    vision.VisionPage(
                        image_bytes=_render_page_png(page), page=page_number, mime_type="image/png"
                    )
                )
            # A near-empty page that went to vision is represented by the vision piece alone; every other page
            # keeps its text layer, including pages whose tables or charts also go to vision.
            if not (wants_vision and len(text) < vision_select.SCANNED_PAGE_CHAR_THRESHOLD):
                pages.append({"page": page_number, "text": text, "extraction_method": "text"})
    finally:
        doc.close()

    if vision_queue:
        pages.extend(vision.extract_pages(vision_queue))

    pages.sort(key=lambda piece: piece["page"])
    return pages


def _render_page_png(page: "pymupdf.Page") -> bytes:
    return page.get_pixmap(dpi=_RENDER_DPI).tobytes("png")
