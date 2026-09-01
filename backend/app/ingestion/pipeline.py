from dataclasses import dataclass, field

from app.ingestion.router import UnsupportedFileType, route_file
from app.ingestion.splitter import split


@dataclass
class FileError:
    filename: str
    reason: str


@dataclass
class IngestResult:
    chunks: list = field(default_factory=list)
    succeeded: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def ingest_files(files: list[tuple[str, bytes]], corpus_scope: str) -> IngestResult:
    """Run every file through routing, extraction, and splitting.

    Per D-19: an unsupported, corrupt, or unreadable file is skipped with
    a clear per-file error, and the batch continues — one bad file in a
    40-document ingest must not kill the rest.
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
                chunks = split(piece["text"], metadata)
                for chunk in chunks:
                    chunk["corpus_scope"] = corpus_scope
                result.chunks.extend(chunks)
        except Exception as exc:
            result.failed.append(FileError(filename=filename, reason=str(exc)))
            continue

        result.succeeded.append(filename)

    return result