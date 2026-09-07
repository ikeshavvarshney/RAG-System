from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.ingestion.pipeline import FileError, ingest_files
from app.shared.session_store import (
    InvalidSessionId,
    drop_session,
    get_session_stores,
    new_session_id,
    scope_for,
    validate_issued_session_id,
)

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
async def ingest(files: list[UploadFile], session_id: str | None = Form(default=None)):
    """Ingest documents into the corpus, or into one session's own store.

    Without ``session_id`` the documents go to the persistent corpus. With one,
    they go to that session's separate store and are tagged
    ``corpus_scope="session:{id}"``. The default is the corpus because loading
    the research corpus is the unattended path; a user upload is the one that
    knows which session it belongs to and says so.
    """
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

    if session_id is None:
        result = ingest_files(file_payloads, corpus_scope="persistent")
    else:
        try:
            validate_issued_session_id(session_id)
            vector_store, keyword_index = get_session_stores(session_id)
        except InvalidSessionId as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        "corpus_scope": "persistent" if session_id is None else scope_for(session_id),
    }


@router.post("/session")
async def create_session():
    """Issue a session id for scoping uploads.

    The id is generated here rather than accepted from the client because it is
    the only thing protecting a session's uploads: there is no authentication,
    so a guessable id would let anyone write into or delete someone else's
    session.
    """
    return {"session_id": new_session_id()}


@router.get("/session/{session_id}/documents")
async def list_session_documents(session_id: str):
    """What this session currently holds.

    The frontend keeps its uploaded-document list in component state, which a
    page reload discards; this is how it recovers. There is deliberately no
    endpoint that enumerates *all* sessions: with no authentication the id is
    the only thing protecting a session, so listing ids would hand every
    visitor everyone else's uploads.
    """
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
    """Remove one uploaded document from a session (USERDOC-02).

    ``source_doc`` is a filename and may contain characters that need escaping
    in a URL, so the path converter is used to take it whole. It never becomes
    a filesystem path: it is only ever matched against chunk metadata.
    """
    try:
        validate_issued_session_id(session_id)
        vector_store, keyword_index = get_session_stores(session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    removed = vector_store.delete_by_document(source_doc, scope_for(session_id))
    if removed:
        # D-22: BM25 is rebuilt in full after any mutation, never patched.
        keyword_index.rebuild()

    return {
        "session_id": session_id,
        "source_doc": source_doc,
        "deleted_chunks": len(removed),
    }


@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Drop a session's uploads entirely (USERDOC-02, whole-session form).

    Removes the session's store directory, so nothing survives in either index.
    Deleting a session that was never created is not an error: the caller's
    intent, that the session hold nothing, is satisfied either way.
    """
    try:
        validate_issued_session_id(session_id)
        removed = drop_session(session_id)
    except InvalidSessionId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"session_id": session_id, "deleted": removed}