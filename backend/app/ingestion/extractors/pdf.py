import pymupdf

SCANNED_PAGE_CHAR_THRESHOLD = 50


def extract(content: bytes, filename: str):
    """Extract text from a PDF, page by page.

    Pages with a usable text layer are extracted directly. Pages with too
    little extracted text are flagged as scanned (OCR path lands separately).
    """
    doc = pymupdf.open(stream=content, filetype="pdf")
    pages = []

    for page_number, page in enumerate(doc, start=1):
        text = page.get_text().strip()

        if len(text) < SCANNED_PAGE_CHAR_THRESHOLD:
            pages.append({
                "page": page_number,
                "text": text,
                "extraction_method": "scanned",  # OCR path, wired in later
            })
        else:
            pages.append({
                "page": page_number,
                "text": text,
                "extraction_method": "text",
            })

    doc.close()
    return pages