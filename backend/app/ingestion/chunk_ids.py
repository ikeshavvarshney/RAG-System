"""The one place a chunk id is made.

An id is a stable hash of everything that identifies a chunk, so ingesting the same file again produces the same
ids and the upsert overwrites instead of duplicating. Nothing else in the code base may create chunk ids.
"""

import hashlib

ID_HEX_CHARS = 24
_SEPARATOR = "\x1f"


def make_chunk_id(
    *,
    corpus_scope: str,
    source_doc: str,
    extraction_method: str,
    chunk_type: str,
    page: int | None,
    location: str | None,
    index: int,
    text: str,
) -> str:
    """Deterministic id from the chunk's scope, source, position and text.

    ``index`` is the chunk's position within its source unit (one page, or one DOCX paragraph or cell), so the
    same text on two pages, or twice in one unit, never collides.
    """
    text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = _SEPARATOR.join(
        [
            corpus_scope,
            source_doc,
            extraction_method,
            chunk_type,
            "" if page is None else str(page),
            location or "",
            str(index),
            text_digest,
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_HEX_CHARS]
