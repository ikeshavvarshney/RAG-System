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
HIGHLIGHT_COLOR = (0.737, 0.816, 0.965)
MAX_SEARCHES = 60
_PASSAGE_FIELDS = {"chunk_id", "text", "page", "location", "chunk_type", "extraction_method"}
_SENTENCE_END = re.compile(r"\.(?=\s|$)")
_MIN_PROSE_CHARS = 24
_MIN_CELL_CHARS = 4
_MAX_SEARCH_CHARS = 90
_CELL_PADDING = 14  # about one line of body text, in PDF points


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


def _fit(words: list[str]) -> str:
    piece = ""
    for word in words:
        if piece and len(piece) + 1 + len(word) > _MAX_SEARCH_CHARS:
            break
        piece = f"{piece} {word}" if piece else word
    return piece


def _windows(sentence: str):
    """A long sentence in word-aligned pieces of at most _MAX_SEARCH_CHARS, so its end is searched too.

    The last piece reaches back from the sentence end rather than being a leftover word or two, which would
    match all over the page.
    """
    words = sentence.split()
    start = 0
    while start < len(words):
        piece = _fit(words[start:])
        start += len(piece.split())
        if start < len(words) and len(" ".join(words[start:])) < _MIN_PROSE_CHARS:
            yield piece
            yield " ".join(reversed(_fit(list(reversed(words))).split()))
            return
        yield piece


def _segments(text: str):
    """Searchable pieces of a passage as (text, is_cell): sentences of prose, and each cell of a table row.

    Splitting only at sentence ends keeps decimals such as 26.0 intact, and table cells are searched one by one
    because a row's figures sit in separate text spans on the page. A text-layer table arrives one cell per line,
    so a short line is searched whole.
    """
    for line in text.splitlines():
        line = line.strip()
        if _MIN_CELL_CHARS <= len(line) < _MIN_PROSE_CHARS and "|" not in line:
            yield line, True
            continue
        if "|" in line:
            for cell in line.split("|"):
                cell = cell.strip()
                if len(cell) >= _MIN_CELL_CHARS and not set(cell) <= set("-: "):
                    yield cell, True
            continue
        for part in _SENTENCE_END.split(line):
            part = part.strip()
            if len(part) >= _MIN_PROSE_CHARS:
                for window in _windows(part):
                    yield window, False


def _is_single_match(rects: list["pymupdf.Rect"]) -> bool:
    """Whether search hits are one occurrence: one rect per line, each line directly below the last."""
    ordered = sorted(rects, key=lambda rect: rect.y0)
    return all(
        0 <= below.y0 - above.y1 < _CELL_PADDING / 2 and below.x0 < above.x1
        for above, below in zip(ordered, ordered[1:])
    )


def _merge(rects: list["pymupdf.Rect"]) -> list["pymupdf.Rect"]:
    """Union rects that overlap or touch on one line, so overlapping searches do not tint text twice."""
    merged: list[pymupdf.Rect] = []
    for rect in sorted(rects, key=lambda r: (round(r.y0), r.x0)):
        last = merged[-1] if merged else None
        if last is not None and abs(last.y0 - rect.y0) < 2 and rect.x0 <= last.x1 + 1:
            merged[-1] = last | rect
        else:
            merged.append(pymupdf.Rect(rect))
    return merged


def _highlight(page: "pymupdf.Page", text: str) -> None:
    """Mark the parts of a passage that can be found in the page's text layer.

    A cell such as "26.0" or a stock phrase can also occur outside the passage. Prose that matches exactly once
    anchors where the passage sits, and every hit outside that vertical span is dropped; with no such anchor
    (a table on its own) every hit is kept.
    """
    hits: list[pymupdf.Rect] = []
    anchors: list[pymupdf.Rect] = []
    for segment, is_cell in list(_segments(text))[:MAX_SEARCHES]:
        found = page.search_for(segment)
        hits.extend(found)
        if not is_cell and found and _is_single_match(found):
            anchors.extend(found)
    if anchors:
        top = min(rect.y0 for rect in anchors) - _CELL_PADDING
        bottom = max(rect.y1 for rect in anchors) + _CELL_PADDING
        hits = [rect for rect in hits if rect.y1 >= top and rect.y0 <= bottom]
    rects = _merge(hits)
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
