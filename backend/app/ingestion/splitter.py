import uuid

import tiktoken

CHUNK_TARGET_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50

_encoding = tiktoken.get_encoding("cl100k_base")


def split(text: str, metadata: dict) -> list[dict]:
    """Split text into ~400-token chunks with 50-token overlap.

    Placeholder splitter (D-20) — Week 3 replaces this with a
    structure-aware splitter. Interface stays identical so the swap
    is a one-line change.
    """
    tokens = _encoding.encode(text)

    if len(tokens) <= CHUNK_TARGET_TOKENS:
        return [_build_chunk(tokens, metadata)]

    chunks = []
    start = 0
    step = CHUNK_TARGET_TOKENS - CHUNK_OVERLAP_TOKENS

    while start < len(tokens):
        end = min(start + CHUNK_TARGET_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(_build_chunk(chunk_tokens, metadata))

        if end == len(tokens):
            break
        start += step

    return chunks


def _build_chunk(tokens: list[int], metadata: dict) -> dict:
    return {
        "chunk_id": str(uuid.uuid4()),
        "text": _encoding.decode(tokens),
        "source_doc": metadata.get("source_doc"),
        "page": metadata.get("page"),
        "location": metadata.get("location"),
        "extraction_method": metadata.get("extraction_method"),
    }