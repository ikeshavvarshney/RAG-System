from app.ingestion.extractors import pdf, docx, image


class UnsupportedFileType(Exception):
    """Raised when a file's detected type has no matching extractor."""

    def __init__(self, filename: str, detected_type: str):
        self.filename = filename
        self.detected_type = detected_type
        super().__init__(
            f"Unsupported file type for '{filename}': detected as '{detected_type}'"
        )


def detect_file_type(filename: str, content: bytes) -> str:
    """Detect the real file type from magic bytes, not just the extension."""
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        # DOCX is a zip archive; extension can lie, so trust the bytes.
        return "docx"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    raise UnsupportedFileType(filename, detected_type="unknown")


_EXTRACTOR_MAP = {
    "pdf": pdf.extract,
    "docx": docx.extract,
    "jpeg": image.extract,
    "png": image.extract,
}


def route_file(filename: str, content: bytes):
    """Detect the real file type and dispatch to the matching extractor."""
    file_type = detect_file_type(filename, content)
    extractor = _EXTRACTOR_MAP.get(file_type)

    if extractor is None:
        raise UnsupportedFileType(filename, detected_type=file_type)

    return extractor(content, filename)