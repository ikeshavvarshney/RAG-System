import io

from fastapi.testclient import TestClient
import pymupdf

from app.main import create_app


def _make_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_ingest_valid_pdf_returns_nonzero_chunk_count():
    client = TestClient(create_app())
    pdf_bytes = _make_pdf_bytes("A valid PDF with plenty of real readable text content here.")

    response = client.post(
        "/api/ingest",
        files={"files": ("report.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] > 0
    assert "report.pdf" in body["succeeded"]
    assert body["failed"] == []


def test_ingest_unsupported_file_returns_200_with_failure_recorded():
    client = TestClient(create_app())

    response = client.post(
        "/api/ingest",
        files={"files": ("notes.txt", io.BytesIO(b"plain text content"), "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] == 0
    assert body["succeeded"] == []
    assert len(body["failed"]) == 1
    assert body["failed"][0]["filename"] == "notes.txt"


def test_ingest_route_registered_in_app():
    app = create_app()
    schema = app.openapi()
    assert "/api/ingest" in schema["paths"]