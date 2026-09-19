from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.core.config import settings
from app.ingestion.pipeline import FileError, ingest_files
from app.query import cache
from app.shared.session_store import (
    PERSISTENT_SCOPE,
    InvalidSessionId,
    drop_session,
    get_session_stores,
    new_session_id,
    scope_for,
    validate_issued_session_id,
)

router = APIRouter()

# The research corpus is 50 documents and is uploaded in one request, so the cap has to clear 50
# rather than sit just under it.
MAX_FILES_PER_REQUEST = 60
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# File type is not validated here.


@router.post("/ingest")
async def ingest(files: list[UploadFile], session_id: str | None = Form(default=None)):
    """Ingest documents into the corpus, or into one session's own store."""
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

    if session_id is None:
        result = ingest_files(file_payloads, corpus_scope=PERSISTENT_SCOPE)
    else:
        try:
            validate_issued_session_id(session_id)
            vector_store, keyword_index = get_session_stores(session_id)
        except InvalidSessionId as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        held = {doc.source_doc for doc in vector_store.documents()}
        incoming = {name for name, _ in file_payloads}
        limit = settings.SESSION_MAX_DOCUMENTS
        if len(held | incoming) > limit:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Session limit is {limit} documents: {len(held)} already "
                    f"uploaded, {len(incoming - held)} new in this request"
                ),
            )

        result = ingest_files(
            file_payloads,
            corpus_scope=scope_for(session_id),
            vector_store=vector_store,
            keyword_index=keyword_index,
        )

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
        "corpus_scope": PERSISTENT_SCOPE if session_id is None else scope_for(session_id),
    }


@router.post("/session")
async def create_session():
    """Issue a session id for scoping uploads."""
    return {"session_id": new_session_id()}


@router.get("/session/{session_id}/documents")
async def list_session_documents(session_id: str):
    """What this session currently holds."""
    try:
        validate_issued_session_id(session_id)
        vector_store, _ = get_session_stores(session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "session_id": session_id,
        "documents": [
            {
                "source_doc": doc.source_doc,
                "chunk_count": doc.chunk_count,
                "pages": doc.pages,
                "extraction_methods": doc.extraction_methods,
            }
            for doc in vector_store.documents()
        ],
    }


@router.delete("/session/{session_id}/documents/{source_doc:path}")
async def delete_session_document(session_id: str, source_doc: str):
    """Remove one uploaded document from a session (USERDOC-02)."""
    try:
        validate_issued_session_id(session_id)
        vector_store, keyword_index = get_session_stores(session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    removed = vector_store.delete_by_document(source_doc, scope_for(session_id))
    if removed:
        # D-22: BM25 is rebuilt in full after any mutation, never patched.
        keyword_index.rebuild()

    # Cached answers citing this document would otherwise keep quoting it.
    invalidated = cache.invalidate_by_document(source_doc, scope_for(session_id))

    return {
        "session_id": session_id,
        "source_doc": source_doc,
        "deleted_chunks": len(removed),
        "invalidated_cache_entries": invalidated,
    }


@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Drop a session's uploads entirely (USERDOC-02, whole-session form)."""
    try:
        validate_issued_session_id(session_id)
        removed = drop_session(session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invalidated = cache.invalidate_scope(scope_for(session_id))

    return {
        "session_id": session_id,
        "deleted": removed,
        "invalidated_cache_entries": invalidated,
    }