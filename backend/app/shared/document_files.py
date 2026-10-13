"""Original uploaded files, kept so the document viewer can show the page an answer cites."""

import logging
from pathlib import Path

from app.core.config import settings
from app.shared.session_store import PERSISTENT_SCOPE, session_path

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "session:"
_SESSION_FILES_DIR = "files"


def _directories(corpus_scope: str) -> list[Path]:
    if corpus_scope == PERSISTENT_SCOPE:
        # The fetched corpus is the fallback for documents ingested by the script rather than uploaded.
        return [Path(settings.DOCUMENTS_PATH), Path(settings.CORPUS_FILES_DIR)]
    return [session_path(corpus_scope.removeprefix(_SESSION_PREFIX)) / _SESSION_FILES_DIR]


def _inside(directory: Path, source_doc: str) -> Path | None:
    name = Path(source_doc).name
    if not name or name != source_doc:
        return None
    path = (directory / name).resolve()
    return path if path.is_relative_to(directory.resolve()) else None


def save(corpus_scope: str, source_doc: str, content: bytes) -> None:
    path = _inside(_directories(corpus_scope)[0], source_doc)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError:
        logger.warning("could not keep the original of %s for the viewer", source_doc, exc_info=True)


def find(corpus_scope: str, source_doc: str) -> Path | None:
    for directory in _directories(corpus_scope):
        path = _inside(directory, source_doc)
        if path is not None and path.is_file():
            return path
    return None


def delete(corpus_scope: str, source_doc: str) -> None:
    path = _inside(_directories(corpus_scope)[0], source_doc)
    if path is not None:
        path.unlink(missing_ok=True)
