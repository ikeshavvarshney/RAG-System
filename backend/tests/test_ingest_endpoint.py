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


def test_ingest_response_includes_index_breakdown():
    client = TestClient(create_app())
    pdf_bytes = _make_pdf_bytes(
        "A valid PDF with plenty of real readable text content, long enough to chunk."
    )

    response = client.post(
        "/api/ingest",
        files={"files": ("report.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    index = body["indexed"]
    assert index["total"] == body["chunk_count"]
    assert set(index["by_extraction_method"]) == {"text", "ocr", "vision"}
    assert index["by_extraction_method"]["text"] >= 1
    assert index["by_extraction_method"]["ocr"] == 0
    assert index["by_extraction_method"]["vision"] == 0
    # only ingest in this (isolated) run -> store totals equal what we just wrote
    assert index["vector_store_total"] == index["total"]
    assert index["keyword_index_total"] == index["total"]


def test_ingest_failure_still_recorded_alongside_zeroed_index_breakdown():
    client = TestClient(create_app())

    response = client.post(
        "/api/ingest",
        files={"files": ("notes.txt", io.BytesIO(b"plain text content"), "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] == 0
    assert len(body["failed"]) == 1
    assert body["failed"][0]["filename"] == "notes.txt"
    assert body["indexed"]["total"] == 0
    assert body["indexed"]["by_extraction_method"] == {"text": 0, "ocr": 0, "vision": 0}

# --------------------------------------------------------------------------- #
# Request limits
# --------------------------------------------------------------------------- #

def test_batch_over_file_limit_is_rejected_with_413():
    """An over-limit batch must be an error status, not a 200 carrying a note.

    The uploader script checks the status code; a 200 with an "error" key
    reads as a successful ingest that produced no chunks.
    """
    from app.api.routes.ingest import MAX_FILES_PER_REQUEST

    client = TestClient(create_app())
    files = [
        ("files", (f"doc{i}.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf"))
        for i in range(MAX_FILES_PER_REQUEST + 1)
    ]

    response = client.post("/api/ingest", files=files)

    assert response.status_code == 413
    assert str(MAX_FILES_PER_REQUEST) in response.json()["detail"]


def test_file_limit_clears_the_research_corpus():
    """The 50-document corpus is uploaded in one request (see docs/03)."""
    from app.api.routes.ingest import MAX_FILES_PER_REQUEST

    assert MAX_FILES_PER_REQUEST >= 50


def test_oversized_file_is_reported_as_too_large_not_as_corrupt(monkeypatch):
    """Size and corruption are different problems and must read differently."""
    from app.api.routes import ingest as ingest_route

    monkeypatch.setattr(ingest_route, "MAX_FILE_SIZE_BYTES", 10)

    client = TestClient(create_app())
    response = client.post(
        "/api/ingest",
        files={"files": ("huge.pdf", io.BytesIO(b"x" * 50), "application/pdf")},
    )

    assert response.status_code == 200
    failed = response.json()["failed"]
    assert len(failed) == 1
    assert failed[0]["filename"] == "huge.pdf"
    assert "too large" in failed[0]["reason"].lower()


def test_oversized_file_does_not_block_the_rest_of_the_batch(monkeypatch):
    """D-19: one rejected file must not cost the batch its valid documents."""
    from app.api.routes import ingest as ingest_route

    monkeypatch.setattr(ingest_route, "MAX_FILE_SIZE_BYTES", 5000)

    pdf_bytes = _make_pdf_bytes("A valid PDF with plenty of real readable text content here.")
    client = TestClient(create_app())

    response = client.post(
        "/api/ingest",
        files=[
            ("files", ("huge.pdf", io.BytesIO(b"x" * 6000), "application/pdf")),
            ("files", ("report.pdf", io.BytesIO(pdf_bytes), "application/pdf")),
        ],
    )

    body = response.json()
    assert "report.pdf" in body["succeeded"]
    assert [f["filename"] for f in body["failed"]] == ["huge.pdf"]
    assert body["chunk_count"] > 0
