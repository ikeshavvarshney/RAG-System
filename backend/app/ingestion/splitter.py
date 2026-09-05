"""Structure-aware text splitter (D-12, INGEST-03).

Replaces the Week 2 fixed-size placeholder. The public interface is unchanged:

    split(text: str, metadata: dict) -> list[dict]

Behaviour:
  * Normal prose is chunked with LangChain's ``RecursiveCharacterTextSplitter``,
    targeting ``CHUNK_TARGET_TOKENS`` with a tiktoken (cl100k_base) length
    function and heading-aware separators.
  * Markdown table blocks are never split - each is emitted whole as a single
    ``chunk_type="table"`` chunk, even when it exceeds ``CHUNK_MAX_TOKENS``.
  * The nearest preceding markdown heading is carried into each chunk's
    ``location`` field; absent any heading it falls back to
    ``metadata.get("location")``.
  * A sub-minimum trailing text fragment is merged back into the previous text
    chunk instead of standing alone. Table chunks never take part in the merge.

The Week 2 fixed-size splitter is retained verbatim as ``_split_fixed_size`` and
can be re-enabled by setting ``FIXED_SIZE_MODE = True``; ``split()`` then
delegates to it entirely.

``corpus_scope``, ``dense_vector_id`` and ``embedding_model`` are populated
downstream (pipeline / embedder) and are deliberately not set here.
"""

from __future__ import annotations

import logging
import re
import uuid

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings

logger = logging.getLogger(__name__)

# Flip to True to fall back to the Week 2 fixed-size splitter, unchanged.
FIXED_SIZE_MODE = False

_ENCODING_NAME = "cl100k_base"
_ENCODING = tiktoken.get_encoding(_ENCODING_NAME)

# RecursiveCharacterTextSplitter separators, most-structural first: markdown
# H2 boundary, blank line, single newline, then whitespace.
_SEPARATORS = ["\n## ", "\n\n", "\n", " "]
_TEXT_OVERLAP_TOKENS = 50

# A markdown table row - at least a leading and trailing pipe on the line.
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
# An ATX markdown heading line; group 1 is the heading text.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# --- Week 2 fixed-size placeholder constants (used only in FIXED_SIZE_MODE) ---
_FIXED_TARGET_TOKENS = 400
_FIXED_OVERLAP_TOKENS = 50


def split(text: str, metadata: dict) -> list[dict]:
    """Split ``text`` into chunk dicts ready for ``Chunk(**chunk)`` construction."""
    if FIXED_SIZE_MODE:
        return _split_fixed_size(text, metadata)

    if not text or not text.strip():
        return []

    fallback_location = metadata.get("location")
    current_heading: str | None = None
    chunks: list[dict] = []

    for kind, block in _segment_blocks(text):
        if not block.strip():
            continue

        if kind == "table":
            location = (
                current_heading if current_heading is not None else fallback_location
            )
            token_count = _token_len(block)
            if token_count > settings.CHUNK_MAX_TOKENS:
                logger.debug(
                    "table block kept intact at %d tokens "
                    "(exceeds CHUNK_MAX_TOKENS=%d)",
                    token_count,
                    settings.CHUNK_MAX_TOKENS,
                )
            chunks.append(_make_chunk(block, metadata, "table", location))
            continue

        for piece in _text_splitter().split_text(block):
            if not piece.strip():
                continue
            headings = _HEADING_RE.findall(piece)
            if headings:
                current_heading = headings[-1].strip()
            location = (
                current_heading if current_heading is not None else fallback_location
            )
            chunks.append(_make_chunk(piece, metadata, "text", location))

    _attach_heading_only_chunks(chunks)
    _merge_trailing_fragments(chunks)
    return chunks


def _segment_blocks(text: str) -> list[tuple[str, str]]:
    """Partition ``text`` into ordered ``(kind, block_text)`` pairs.

    ``kind`` is ``"table"`` for a run of 2+ consecutive markdown table rows
    (a real table always has a header plus a delimiter row); every other run,
    including a lone stray pipe line, is ``"text"``. Adjacent text runs are
    coalesced so a stray pipe line does not sever surrounding prose.
    """
    lines = text.split("\n")
    is_table = [bool(_TABLE_LINE_RE.match(line)) for line in lines]

    runs: list[tuple[str, list[str]]] = []
    i, n = 0, len(lines)
    while i < n:
        j = i
        while j < n and is_table[j] == is_table[i]:
            j += 1
        kind = "table" if (is_table[i] and (j - i) >= 2) else "text"
        runs.append((kind, lines[i:j]))
        i = j

    merged: list[tuple[str, list[str]]] = []
    for kind, run_lines in runs:
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1][1].extend(run_lines)
        else:
            merged.append((kind, list(run_lines)))

    return [(kind, "\n".join(run_lines)) for kind, run_lines in merged]


def _is_heading_only(text: str) -> bool:
    """True when every non-blank line of ``text`` is a markdown heading."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(_HEADING_RE.match(line) for line in lines)


def _attach_heading_only_chunks(chunks: list[dict]) -> None:
    """Fold a text chunk that is nothing but heading line(s) into the next text
    chunk, so a heading never stands alone as its own fragment. A heading-only
    chunk with no following text chunk is left for the trailing-merge pass.
    """
    i = 0
    while i < len(chunks) - 1:
        cur, nxt = chunks[i], chunks[i + 1]
        if (
            cur["chunk_type"] == "text"
            and nxt["chunk_type"] == "text"
            and _is_heading_only(cur["text"])
        ):
            nxt["text"] = f"{cur['text'].strip()}\n\n{nxt['text'].lstrip()}"
            if cur["location"] is not None:
                nxt["location"] = cur["location"]
            chunks.pop(i)
            continue
        i += 1


def _merge_trailing_fragments(chunks: list[dict]) -> None:
    """Fold a sub-minimum trailing text chunk into the previous text chunk.

    Mutates ``chunks`` in place. A table is never merged into text and text is
    never merged into a table, so the loop stops at the first table boundary.
    """
    while len(chunks) >= 2:
        last, prev = chunks[-1], chunks[-2]
        if last["chunk_type"] != "text" or prev["chunk_type"] != "text":
            break
        if _token_len(last["text"]) >= settings.CHUNK_MIN_TOKENS:
            break
        prev["text"] = f"{prev['text'].rstrip()}\n{last['text'].lstrip()}"
        chunks.pop()


def _text_splitter() -> RecursiveCharacterTextSplitter:
    """A token-length-aware recursive splitter bound to the current config."""
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=_ENCODING_NAME,
        chunk_size=settings.CHUNK_TARGET_TOKENS,
        chunk_overlap=_TEXT_OVERLAP_TOKENS,
        separators=_SEPARATORS,
    )


def _make_chunk(
    text: str, metadata: dict, chunk_type: str, location: str | None
) -> dict:
    """Build a chunk dict with every field ``Chunk`` requires except the ones
    added downstream (``corpus_scope``, ``dense_vector_id``, ``embedding_model``).
    """
    return {
        "chunk_id": str(uuid.uuid4()),
        "text": text,
        "source_doc": metadata.get("source_doc"),
        "page": metadata.get("page"),
        "location": location,
        "chunk_type": chunk_type,
        "extraction_method": metadata.get("extraction_method"),
    }


def _token_len(text: str) -> int:
    return len(_ENCODING.encode(text))


# --------------------------------------------------------------------------- #
# Week 2 fixed-size placeholder - retained unchanged, reachable only when
# FIXED_SIZE_MODE is True.
# --------------------------------------------------------------------------- #
def _split_fixed_size(text: str, metadata: dict) -> list[dict]:
    """Fixed ~400-token windows with 50-token overlap (Week 2 windowing).

    Kept only as a fallback/debug path behind ``FIXED_SIZE_MODE``. Emits the
    same chunk-dict shape as the structure-aware path (``chunk_type="text"``
    for every chunk) so ``Chunk(**chunk_dict)`` stays valid when the flag is on.
    """
    tokens = _ENCODING.encode(text)

    if len(tokens) <= _FIXED_TARGET_TOKENS:
        return [_build_chunk_fixed(tokens, metadata)]

    chunks = []
    start = 0
    step = _FIXED_TARGET_TOKENS - _FIXED_OVERLAP_TOKENS

    while start < len(tokens):
        end = min(start + _FIXED_TARGET_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(_build_chunk_fixed(chunk_tokens, metadata))

        if end == len(tokens):
            break
        start += step

    return chunks


def _build_chunk_fixed(tokens: list[int], metadata: dict) -> dict:
    return {
        "chunk_id": str(uuid.uuid4()),
        "text": _ENCODING.decode(tokens),
        "source_doc": metadata.get("source_doc"),
        "page": metadata.get("page"),
        "location": metadata.get("location"),
        "chunk_type": "text",
        "extraction_method": metadata.get("extraction_method"),
    }
