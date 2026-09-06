from fastapi import APIRouter, HTTPException, UploadFile

from app.ingestion.pipeline import FileError, ingest_files

router = APIRouter()

# The research corpus is 50 documents and is uploaded in one request, so the
# cap has to clear 50 rather than sit just under it. The headroom above that
# is for a corpus that grows; the real ceiling on a batch is ingestion time,
# not this number.
MAX_FILES_PER_REQUEST = 60
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# File type is not validated here. Browsers set Content-Type from the file
# extension, which is exactly what a mislabelled file gets wrong, so trusting
# it would defeat the check. `route_file` sniffs magic bytes instead
# (INGEST-01) and is the single place a type is decided.


@router.post("/ingest")
async def ingest(files: list[UploadFile]):
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many files: {len(files)} sent, max "
                f"{MAX_FILES_PER_REQUEST} per request"
            ),
        )

    file_payloads = []
    oversized: list[FileError] = []

    for upload in files:
        content = await upload.read()

        if len(content) > MAX_FILE_SIZE_BYTES:
            # Reported as its own failure rather than passed on as empty bytes.
            # Empty bytes reach the extractor as an unreadable file, so the
            # uploader is told the document is corrupt when it is merely large.
            oversized.append(
                FileError(
                    filename=upload.filename,
                    reason=(
                        f"File too large: {len(content)} bytes, max "
                        f"{MAX_FILE_SIZE_BYTES} ({MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)"
                    ),
                )
            )
            continue

        file_payloads.append((upload.filename, content))

    result = ingest_files(file_payloads, corpus_scope="persistent")
    failures = oversized + result.failed

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
            "failed": index.failed_chunks if index else 0,
            "failure_reason": index.failure_reason if index else None,
            "vector_store_total": index.vector_store_total if index else 0,
            "keyword_index_total": index.keyword_index_total if index else 0,
        },
        "succeeded": result.succeeded,
        "failed": [
            {"filename": f.filename, "reason": f.reason} for f in failures
        ],
    }