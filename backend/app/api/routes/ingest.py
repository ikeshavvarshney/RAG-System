from fastapi import APIRouter, UploadFile

from app.ingestion.pipeline import ingest_files

router = APIRouter()

MAX_FILES_PER_REQUEST = 40
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
}


@router.post("/ingest")
async def ingest(files: list[UploadFile]):
    if len(files) > MAX_FILES_PER_REQUEST:
        return {
            "error": f"Too many files: max {MAX_FILES_PER_REQUEST} per request",
        }

    file_payloads = []
    for upload in files:
        content = await upload.read()

        if len(content) > MAX_FILE_SIZE_BYTES:
            file_payloads.append((upload.filename, b""))  # will fail extraction
            continue

        file_payloads.append((upload.filename, content))

    result = ingest_files(file_payloads, corpus_scope="persistent")

    index = result.index
    return {
        "chunk_count": len(result.chunks),
        "indexed": {
            "total": index.total_indexed if index else 0,
            "by_extraction_method": (
                index.by_extraction_method
                if index
                else {"text": 0, "ocr": 0, "vision": 0}
            ),
            "vector_store_total": index.vector_store_total if index else 0,
            "keyword_index_total": index.keyword_index_total if index else 0,
        },
        "succeeded": result.succeeded,
        "failed": [
            {"filename": f.filename, "reason": f.reason} for f in result.failed
        ],
    }