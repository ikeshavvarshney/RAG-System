import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.query.generation import (
    _MARKER_GROUP,
    _NUMBER,
    GenerationResult,
    PassageRef,
    is_non_answer,
)
from app.shared.schemas.citation import Citation, CorpusCitation, WebCitation

if TYPE_CHECKING:
    from app.query.pipeline import Stage

# A paragraph needs at least this many words (markers excluded) to count as a claim.
MIN_SUBSTANTIVE_WORDS = 8
NO_SUPPORT_ANSWER = "The retrieved context doesn't support an answer to this question."

_HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*:?\s*$)")
_ADJACENT_DUPLICATE = re.compile(r"(\[\d+\])(?:\s*\1)+")


class RemovedClaim(BaseModel):
    text: str
    reason: str  # "uncited" | "invalid_marker"


class CitationFilterResult(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    removed_claims: list[RemovedClaim] = Field(default_factory=list)
    invalid_markers: list[int] = Field(default_factory=list)
    # Original marker in the generated answer -> number of the surviving citation (1-based).
    marker_map: dict[int, int] = Field(default_factory=dict)
    is_non_answer: bool = False


def _numbers(group: re.Match[str]) -> list[int]:
    return [int(n) for n in _NUMBER.findall(group.group(1))]


def _rewrite_markers(text: str, mapping: Callable[[int], int | None]) -> str:
    """Rewrite each marker group through ``mapping``; None drops that number, and an emptied group vanishes."""

    def replace(group: re.Match[str]) -> str:
        kept: list[int] = []
        for number in _numbers(group):
            target = mapping(number)
            if target is not None and target not in kept:
                kept.append(target)
        return f"[{', '.join(map(str, kept))}]" if kept else ""

    return _ADJACENT_DUPLICATE.sub(r"\1", _MARKER_GROUP.sub(replace, text))


def _tidy(paragraph: str) -> str:
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", paragraph)).strip()


def _is_substantive(paragraph: str) -> bool:
    if _HEADING.match(paragraph) or is_non_answer(paragraph):
        return False
    return len(_MARKER_GROUP.sub("", paragraph).split()) >= MIN_SUBSTANTIVE_WORDS


def _citation_key(ref: PassageRef) -> tuple[str, str | int | None]:
    if ref.kind == "web":
        return ("web", ref.source_url)
    return ("corpus", f"{ref.source_doc}\x00{ref.page}")


def _build_citation(ref: PassageRef) -> Citation:
    if ref.kind == "web":
        url = ref.source_url or ""
        return WebCitation(source_url=url, title=ref.title or url, score=ref.score, snippet=ref.snippet)
    return CorpusCitation(
        source_doc=ref.source_doc or "",
        page=ref.page,
        chunk_id=ref.chunk_id,
        score=ref.score,
        snippet=ref.snippet,
    )


def filter_structural(
    generation: GenerationResult,
    *,
    stage: "Callable[[Stage], AbstractContextManager[None]] | None" = None,
) -> CitationFilterResult:
    """Structural citation checks only: no model call, no network."""
    with stage("citations") if stage else nullcontext():
        return _filter(generation)


def _filter(generation: GenerationResult) -> CitationFilterResult:
    if generation.is_non_answer:
        return CitationFilterResult(answer=generation.answer, is_non_answer=True)

    passages = generation.passages
    removed: list[RemovedClaim] = []
    invalid: list[int] = []

    def drop_invalid(number: int) -> int | None:
        if number in passages:
            return number
        if number not in invalid:
            invalid.append(number)
            removed.append(RemovedClaim(text=f"[{number}]", reason="invalid_marker"))
        return None

    stripped = _rewrite_markers(generation.answer, drop_invalid)

    kept: list[str] = []
    for line in stripped.splitlines():
        paragraph = _tidy(line)
        if not paragraph:
            continue
        has_citation = bool(_MARKER_GROUP.search(paragraph))
        if _is_substantive(paragraph) and not has_citation:
            removed.append(RemovedClaim(text=paragraph, reason="uncited"))
            continue
        kept.append(paragraph)
    text = "\n\n".join(kept) if "\n\n" in generation.answer else "\n".join(kept)

    originals: list[int] = []
    for group in _MARKER_GROUP.finditer(text):
        for number in _numbers(group):
            if number not in originals:
                originals.append(number)
    if not originals:
        return CitationFilterResult(
            answer=NO_SUPPORT_ANSWER,
            removed_claims=removed,
            invalid_markers=invalid,
            is_non_answer=True,
        )

    # One citation per document page (or web URL): the best-scoring passage represents the group.
    groups: dict[tuple, list[int]] = {}
    for number in originals:
        groups.setdefault(_citation_key(passages[number]), []).append(number)
    marker_map: dict[int, int] = {}
    citations: list[Citation] = []
    for position, members in enumerate(groups.values(), start=1):
        best = max(members, key=lambda n: (passages[n].score, -n))
        citations.append(_build_citation(passages[best]))
        for member in members:
            marker_map[member] = position

    return CitationFilterResult(
        answer=_rewrite_markers(text, marker_map.get),
        citations=citations,
        removed_claims=removed,
        invalid_markers=invalid,
        marker_map=marker_map,
    )
