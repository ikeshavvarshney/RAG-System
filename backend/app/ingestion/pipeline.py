import uuid
from dataclasses import dataclass, field

from app.ingestion.indexer import IndexResult, index_chunks
from app.ingestion.router import UnsupportedFileType, route_file
from app.ingestion.splitter import split
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


def ingest_files(
    files: list[tuple[str, bytes]],
    corpus_scope: str,
    *,
    vector_store: VectorStore | None = None,
    keyword_index: KeywordIndex | None = None,
) -> IngestResult:
    """Run every file through routing, extraction, splitting, then indexing.

    Per D-19: an unsupported, corrupt, or unreadable file is skipped with
    a clear per-file error, and the batch continues — one bad file in a
    40-document ingest must not kill the rest.

    After all chunks are produced they are embedded and written to the vector
    store + keyword index (see :func:`app.ingestion.indexer.index_chunks`);
    ``vector_store`` / ``keyword_index`` default to the process singletons.
    """
    result = IngestResult()

    for filename, content in files:
        try:
            extracted_pieces = route_file(filename, content)
        except UnsupportedFileType as exc:
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue
        except Exception as exc:
            # Corrupt/unreadable files raise all sorts of library-specific
            # errors (BadZipFile, FzErrorFormat, etc.) — catch broadly here
            # so one bad file can never take down the batch.
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue

        try:
            for piece in extracted_pieces:
                metadata = {
                    "source_doc": filename,
                    "page": piece.get("page"),
                    "location": piece.get("location"),
                    "extraction_method": piece.get("extraction_method"),
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

    result.index = index_chunks(
        result.chunks,
        vector_store=vector_store,
        keyword_index=keyword_index,
    )
    return result


def _atomic_chunk(piece: dict, metadata: dict) -> dict:
    """Wrap a self-contained vision piece as a single chunk, mirroring the dict
    shape produced by ``splitter._make_chunk`` and preserving the piece's
    ``chunk_type`` (which ``split()`` would otherwise reclassify to ``"text"``).
    """
    return {
        "chunk_id": str(uuid.uuid4()),
        "text": piece["text"],
        "source_doc": metadata["source_doc"],
        "page": metadata["page"],
        "location": metadata["location"],
        "chunk_type": piece["chunk_type"],
        "extraction_method": metadata["extraction_method"],
    }