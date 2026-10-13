"""Document viewer API: a document's indexed passages and its pages rendered as images."""

import re

import pymupdf
from fastapi import APIRouter, HTTPException, Query, Response

from app.api.schemas import ERRORS
from app.ingestion.indexer import get_vector_store
from app.shared import document_files
from app.shared.schemas.chunk import Chunk
from app.shared.session_store import (
    PERSISTENT_SCOPE,
    InvalidSessionId,
    find_session_stores,
    scope_for,
    validate_issued_session_id,
)
from app.shared.vector_store import VectorStore

router = APIRouter()

RENDER_DPI = 110
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
HIGHLIGHT_COLOR = (1.0, 0.86, 0.35)
MAX_SEARCHES = 60
_PASSAGE_FIELDS = {"chunk_id", "text", "page", "location", "chunk_type", "extraction_method"}
_SEGMENT = re.compile(r"[^.\n|]{24,}")


def _scope(session_id: str | None, source_doc: str) -> tuple[str, VectorStore]:
    """The session's store when the session holds this document, otherwise the corpus."""
    if session_id:
        try:
            validate_issued_session_id(session_id)
            stores = find_session_stores(session_id)
        except InvalidSessionId as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if stores is not None and any(doc.source_doc == source_doc for doc in stores[0].documents()):
            return scope_for(session_id), stores[0]
    return PERSISTENT_SCOPE, get_vector_store()


def _kind(path) -> str:
    if path is None:
        return "unavailable"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_TYPES:
        return "image"
    return "docx" if suffix == ".docx" else "unavailable"


def _passage(chunk: Chunk) -> dict:
    return chunk.model_dump(include=_PASSAGE_FIELDS)


def _highlight(page: "pymupdf.Page", text: str) -> None:
    """Mark the parts of a passage that can be found in the page's text layer."""
    for segment in [match.group().strip() for match in _SEGMENT.finditer(text)][:MAX_SEARCHES]:
        rects = page.search_for(segment[:90])
        if rects:
            annotation = page.add_highlight_annot(rects)
            annotation.set_colors(stroke=HIGHLIGHT_COLOR)
            annotation.update()


@router.get("/viewer/document", responses=ERRORS)
def view_document(source_doc: str, session_id: str | None = None) -> dict:
    """What the viewer needs for one document: its type, page count and every indexed passage."""
    scope, store = _scope(session_id, source_doc)
    passages = [chunk for chunk in store.all_chunks() if chunk.source_doc == source_doc]
    path = document_files.find(scope, source_doc)
    if not passages and path is None:
        raise HTTPException(status_code=404, detail=f"No document named {source_doc!r}")

    kind = _kind(path)
    page_count = 0
    if kind == "pdf":
        with pymupdf.open(path) as document:
            page_count = document.page_count
    elif kind == "image":
        page_count = 1

    return {
        "source_doc": source_doc,
        "kind": kind,
        "page_count": page_count,
        "passages": [_passage(chunk) for chunk in sorted(passages, key=lambda c: c.page or 0)],
    }


@router.get("/viewer/page", responses=ERRORS)
def view_page(
    source_doc: str,
    page: int = Query(default=1, ge=1),
    chunk_id: str | None = None,
    session_id: str | None = None,
) -> Response:
    """One page as an image, with the cited passage highlighted where the page has a text layer."""
    scope, store = _scope(session_id, source_doc)
    path = document_files.find(scope, source_doc)
    kind = _kind(path)
    headers = {"Cache-Control": "private, max-age=300"}

    if kind == "image":
        return Response(path.read_bytes(), media_type=IMAGE_TYPES[path.suffix.lower()], headers=headers)
    if kind != "pdf":
        raise HTTPException(status_code=404, detail=f"No page images are available for {source_doc!r}")

    with pymupdf.open(path) as document:
        if page > document.page_count:
            raise HTTPException(status_code=404, detail=f"{source_doc!r} has {document.page_count} pages")
        target = document[page - 1]
        if chunk_id:
            cited = [c for c in store.get([chunk_id]) if c.source_doc == source_doc and c.page == page]
            if cited and cited[0].extraction_method == "text":
                _highlight(target, cited[0].text)
        image = target.get_pixmap(dpi=RENDER_DPI).tobytes("png")
    return Response(image, media_type="image/png", headers=headers)
