import pytest

from app.ingestion.router import UnsupportedFileType, detect_file_type, route_file


# Minimal valid magic-byte headers for each format
PDF_BYTES = b"%PDF-1.4\n%fake pdf content"
DOCX_BYTES = b"PK\x03\x04\x14\x00fake docx zip content"
JPEG_BYTES = b"\xff\xd8\xff\xe0fake jpeg content"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake png content"
TXT_BYTES = b"just some plain text, not a real file format"


def test_detect_pdf():
    assert detect_file_type("report.pdf", PDF_BYTES) == "pdf"


def test_detect_docx():
    assert detect_file_type("report.docx", DOCX_BYTES) == "docx"


def test_detect_jpeg():
    assert detect_file_type("photo.jpg", JPEG_BYTES) == "jpeg"


def test_detect_png():
    assert detect_file_type("photo.png", PNG_BYTES) == "png"


def test_unsupported_type_raises():
    with pytest.raises(UnsupportedFileType):
        detect_file_type("notes.txt", TXT_BYTES)


def test_docx_renamed_as_pdf_routes_to_docx():
    # Extension says .pdf, but the bytes are a DOCX (zip) file.
    # Detection must trust the bytes, not the extension.
    detected = detect_file_type("fake.pdf", DOCX_BYTES)
    assert detected == "docx"


def test_route_file_dispatches_to_pdf_extractor():
    with pytest.raises(NotImplementedError):
        route_file("report.pdf", PDF_BYTES)


def test_route_file_dispatches_to_docx_extractor():
    with pytest.raises(NotImplementedError):
        route_file("report.docx", DOCX_BYTES)


def test_route_file_unsupported_raises():
    with pytest.raises(UnsupportedFileType):
        route_file("notes.txt", TXT_BYTES)