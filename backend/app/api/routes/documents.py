from fastapi import APIRouter, HTTPException

from app.api.schemas import ERRORS, DeleteDocumentResponse, DocumentInfo, DocumentList
from app.ingestion.indexer import get_keyword_index, get_vector_store
from app.query import cache
from app.shared.session_store import PERSISTENT_SCOPE

router = APIRouter()


@router.get("/documents", response_model=DocumentList, responses=ERRORS)
async def list_documents() -> DocumentList:
    """What the persistent corpus currently holds."""
    return DocumentList(
        documents=[
            DocumentInfo(
                source_doc=doc.source_doc,
                chunk_count=doc.chunk_count,
                pages=doc.pages,
                extraction_methods=doc.extraction_methods,
            )
            for doc in get_vector_store().documents()
        ]
    )


@router.delete("/documents/{source_doc:path}", response_model=DeleteDocumentResponse, responses=ERRORS)
async def delete_document(source_doc: str) -> DeleteDocumentResponse:
    """Remove one document from the corpus, its keyword entries and any cached answers citing it (D-23)."""
    removed = get_vector_store().delete_by_document(source_doc, PERSISTENT_SCOPE)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No document named {source_doc!r} in the corpus")
    # D-22: BM25 is rebuilt in full after any mutation, never patched.
    get_keyword_index().rebuild()
    invalidated = cache.invalidate_by_document(source_doc, PERSISTENT_SCOPE)
    return DeleteDocumentResponse(
        source_doc=source_doc, deleted_chunks=len(removed), invalidated_cache_entries=invalidated
    )
