from dataclasses import dataclass, field

from app.ingestion.chunk_ids import make_chunk_id
from app.ingestion.indexer import IndexResult, get_keyword_index, get_vector_store, index_chunks
from app.ingestion.router import UnsupportedFileType, route_file
from app.ingestion.splitter import split
from app.ingestion.vision_select import VisionSelection, plan_vision
from app.shared.keyword_index import KeywordIndex
from app.shared.vector_store import VectorStore

# Vision pieces of these types are already short, self-contained structured
# descriptions (a chart summary, a figure caption). Running them through split()
# would only strip the chunk_type the vision extractor assigned and reclassify
# them as plain "text", so they bypass split() and become one chunk directly.
# "text" (plain transcriptions, OCR fallback) and "table" (markdown tables,
# kept intact by the splitter's own table protection) still go through split().
_ATOMIC_CHUNK_TYPES = {"chart", "image_caption"}


@dataclass
class FileError:
    filename: str
    reason: str


@dataclass
class IngestResult:
    chunks: list = field(default_factory=list)
    succeeded: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    index: IndexResult | None = None
    vision: VisionSelection | None = None


def ingest_files(
    files: list[tuple[str, bytes]],
    corpus_scope: str,
    *,
    vector_store: VectorStore | None = None,
    keyword_index: KeywordIndex | None = None,
    vision_strict: bool = True,
    chart_dense_docs: frozenset[str] | set[str] = frozenset(),
) -> IngestResult:
    """Run every file through routing, extraction, splitting, then indexing.

    Vision pages are chosen for the whole batch first, locally, before any model call. With ``vision_strict`` a
    batch whose plan exceeds ``MAX_VISION_PAGES`` raises ``VisionPageCapExceeded`` at that point; without it the
    plan is truncated and the dropped pages are recorded in ``result.vision``.
    """
    result = IngestResult()
    result.vision = plan_vision(files, strict=vision_strict, chart_dense_docs=chart_dense_docs)

    for filename, content in files:
        try:
            extracted_pieces = route_file(filename, content, result.vision.units_for(filename))
        except UnsupportedFileType as exc:
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue
        except Exception as exc:
            # Corrupt/unreadable files raise all sorts of library-specific errors (BadZipFile,
            # FzErrorFormat, etc.) — catch broadly here so one bad file can never take down the
            # batch.
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue

        try:
            for piece in extracted_pieces:
                metadata = {
                    "source_doc": filename,
                    "page": piece.get("page"),
                    "location": piece.get("location"),
                    "extraction_method": piece.get("extraction_method"),
                    "corpus_scope": corpus_scope,
                }
                if piece.get("chunk_type") in _ATOMIC_CHUNK_TYPES:
                    chunks = (
                        [_atomic_chunk(piece, metadata)]
                        if piece["text"].strip()
                        else []
                    )
                else:
                    chunks = split(piece["text"], metadata)
                for chunk in chunks:
                    chunk["corpus_scope"] = corpus_scope
                result.chunks.extend(chunks)
        except Exception as exc:
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue

        result.succeeded.append(filename)

    # Deterministic ids make an identical chunk a no-op upsert, but two chunks that hash alike within one
    # batch would still collide inside it, so keep the first.
    unique: dict[str, dict] = {}
    for chunk in result.chunks:
        unique.setdefault(chunk["chunk_id"], chunk)
    result.chunks = list(unique.values())

    store = vector_store if vector_store is not None else get_vector_store()
    keywords = keyword_index if keyword_index is not None else get_keyword_index()

    # A changed file has different chunk ids, so its old version would survive beside the new one. Clear each
    # re-ingested document first. A file that failed extraction keeps its existing chunks.
    cleared = sum(len(store.delete_by_document(name, corpus_scope)) for name in result.succeeded)

    result.index = index_chunks(result.chunks, vector_store=store, keyword_index=keywords)
    if cleared and result.index.total_indexed == 0:
        keywords.rebuild()  # index_chunks only rebuilds after a write; deleting alone also stales BM25
        result.index.keyword_index_total = len(keywords)
    return result


def _atomic_chunk(piece: dict, metadata: dict) -> dict:
    """Wrap a self-contained vision piece as a single chunk, mirroring the dict shape produced by ``splitter._make_chunk`` and preserving the piece's ``chunk_type`` (which ``split()`` would otherwise reclassify to ``"text"``)."""
    return {
        "chunk_id": make_chunk_id(
            corpus_scope=metadata["corpus_scope"],
            source_doc=metadata["source_doc"],
            extraction_method=metadata["extraction_method"],
            chunk_type=piece["chunk_type"],
            page=metadata["page"],
            location=metadata["location"],
            index=0,
            text=piece["text"],
        ),
        "text": piece["text"],
        "source_doc": metadata["source_doc"],
        "page": metadata["page"],
        "location": metadata["location"],
        "chunk_type": piece["chunk_type"],
        "extraction_method": metadata["extraction_method"],
    }