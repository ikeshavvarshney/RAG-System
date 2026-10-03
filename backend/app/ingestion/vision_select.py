"""Which pages and images go to the vision model, decided globally and before any API call.

Every source (standalone images, DOCX embedded images, PDF pages) contributes scored candidates. Selection keeps the
best ones within ``settings.MAX_VISION_PAGES`` and spreads them across documents. Counting happens here, locally, so
the cap is a plan rather than something discovered halfway through an ingest run.

Scanned pages (no text layer and a page-sized image) are not vision candidates: they have no text to enrich and go
straight to OCR (D-17), which is local and free.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from app.core.config import settings
from app.ingestion.extractors.vision import VisionPageCapExceeded

logger = logging.getLogger(__name__)

# --- page features ------------------------------------------------------------------------------------------------
# A page with fewer text characters than this has no usable text layer.
SCANNED_PAGE_CHAR_THRESHOLD = 50
# Image area / page area at which an image is "page-sized" (a scan or a full-bleed figure).
LARGE_IMAGE_COVERAGE = 0.50
# Below this much image area an image is ignored (rules, bullets, small logos).
MIN_IMAGE_COVERAGE = 0.02
# "Low text" for the medium signal: a caption-sized amount of text beside an image.
LOW_TEXT_CHAR_LIMIT = 100
# A detected table counts only at this size; 2x2 and 3x2 detections are mostly layout boxes.
MIN_TABLE_ROWS = 4
MIN_TABLE_COLS = 3
# Vector drawings per page at which a page is probably a chart (table borders alone rarely reach this).
MIN_CHART_DRAWINGS = 300

# --- scoring ------------------------------------------------------------------------------------------------------
TABLE_SCORE = 3.0  # strong
CHART_DRAWINGS_SCORE = 3.0  # strong
LARGE_IMAGE_SCORE = 3.0  # strong
LOW_TEXT_IMAGE_SCORE = 1.0  # medium
# Applied to a page that already scores, from a document the manifest marks chart-dense: wins ties.
CHART_DENSE_BONUS = 0.5
# Images are chosen on purpose (INGEST-02 sends every standalone image to vision), so they outrank page guesses.
STANDALONE_IMAGE_SCORE = 10.0
DOCX_IMAGE_SCORE = 6.0
# A candidate needs at least one strong signal.
MIN_SELECT_SCORE = 3.0

# --- spreading ----------------------------------------------------------------------------------------------------
# At most this many pages of one document: coverage across documents beats depth in one.
VISION_MAX_PAGES_PER_DOC = 6

CandidateKind = Literal["pdf_page", "docx_image", "image"]


@dataclass(frozen=True)
class PageFeatures:
    text_chars: int
    image_coverage: float
    drawings: int = 0
    tables: tuple[tuple[int, int], ...] = ()  # (rows, cols) of every detected table

    @property
    def is_scanned(self) -> bool:
        return self.text_chars < SCANNED_PAGE_CHAR_THRESHOLD and self.image_coverage >= LARGE_IMAGE_COVERAGE


@dataclass(frozen=True)
class VisionCandidate:
    source_doc: str
    unit: int  # PDF page number, DOCX image number, or 1 for a standalone image
    kind: CandidateKind
    score: float
    reason: str

    @property
    def label(self) -> int | str:
        """The page number, or the location string an image chunk would carry."""
        if self.kind == "pdf_page":
            return self.unit
        return f"embedded_image_{self.unit}" if self.kind == "docx_image" else "image"


@dataclass
class FileAnalysis:
    candidates: list[VisionCandidate] = field(default_factory=list)
    scanned_pages: list[int] = field(default_factory=list)


@dataclass
class VisionSelection:
    selected: list[VisionCandidate] = field(default_factory=list)
    dropped: list[tuple[VisionCandidate, str]] = field(default_factory=list)  # candidate, why
    scanned_to_ocr: list[tuple[str, int]] = field(default_factory=list)  # (source_doc, page)
    cap: int = 0
    strict: bool = True
    qualified: int = 0  # candidates with a strong signal, before the ceiling and the cap

    def units_for(self, source_doc: str) -> set[int]:
        return {c.unit for c in self.selected if c.source_doc == source_doc}

    def ocr_pages_for(self, source_doc: str) -> set[int]:
        return {page for doc, page in self.scanned_to_ocr if doc == source_doc}


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def image_coverage(page: "pymupdf.Page") -> float:
    """Fraction of the page area covered by placed raster images (capped at 1.0)."""
    page_area = abs(page.rect.width * page.rect.height)
    if page_area == 0:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        covered += abs((x1 - x0) * (y1 - y0))
    return min(covered / page_area, 1.0)


def page_features(page: "pymupdf.Page") -> PageFeatures:
    """Local, call-free measurements of one PDF page. Scanned pages skip the costly drawing and table passes."""
    text_chars = len(page.get_text().strip())
    coverage = image_coverage(page)
    base = PageFeatures(text_chars, coverage)
    if base.is_scanned:
        return base
    tables: list[tuple[int, int]] = []
    try:
        tables = [(t.row_count, t.col_count) for t in page.find_tables().tables]
    except Exception:  # noqa: BLE001 - table detection is a signal, never a reason to fail a page
        logger.debug("table detection failed on a page", exc_info=True)
    return PageFeatures(text_chars, coverage, len(page.get_drawings()), tuple(tables))


def score_page(features: PageFeatures, *, chart_dense: bool = False) -> tuple[float, str]:
    """(score, reason) for a PDF page. Scanned pages score 0: they go to OCR, not vision."""
    if features.is_scanned:
        return 0.0, "scanned page (OCR)"
    score = 0.0
    reasons: list[str] = []

    sized = [t for t in features.tables if t[0] >= MIN_TABLE_ROWS and t[1] >= MIN_TABLE_COLS]
    if sized:
        rows, cols = max(sized, key=lambda t: t[0] * t[1])
        score += TABLE_SCORE
        reasons.append(f"table {rows}x{cols}")
    if features.drawings >= MIN_CHART_DRAWINGS:
        score += CHART_DRAWINGS_SCORE
        reasons.append(f"{features.drawings} vector drawings")
    if features.image_coverage >= LARGE_IMAGE_COVERAGE:
        score += LARGE_IMAGE_SCORE
        reasons.append(f"image covers {features.image_coverage:.0%} of the page")
    elif features.text_chars < LOW_TEXT_CHAR_LIMIT and features.image_coverage >= MIN_IMAGE_COVERAGE:
        score += LOW_TEXT_IMAGE_SCORE
        reasons.append("little text beside an image")

    if score > 0 and chart_dense:
        score += CHART_DENSE_BONUS
        reasons.append("chart-dense document")
    return score, ", ".join(reasons) or "no visual signal"


# --------------------------------------------------------------------------- #
# Candidates per file
# --------------------------------------------------------------------------- #
def analyze_pdf(doc: "pymupdf.Document", filename: str, *, chart_dense: bool = False) -> FileAnalysis:
    analysis = FileAnalysis()
    for number, page in enumerate(doc, start=1):
        features = page_features(page)
        if features.is_scanned:
            analysis.scanned_pages.append(number)
            continue
        score, reason = score_page(features, chart_dense=chart_dense)
        if score > 0:
            analysis.candidates.append(VisionCandidate(filename, number, "pdf_page", score, reason))
    return analysis


def analyze_file(filename: str, content: bytes, *, chart_dense: bool = False) -> FileAnalysis:
    """Candidates of one file. A file that cannot be read has none; extraction reports it properly."""
    from app.ingestion import router
    from app.ingestion.extractors import docx as docx_extractor

    try:
        file_type = router.detect_file_type(filename, content)
        if file_type == "pdf":
            doc = pymupdf.open(stream=content, filetype="pdf")
            try:
                return analyze_pdf(doc, filename, chart_dense=chart_dense)
            finally:
                doc.close()
        if file_type == "docx":
            plan = docx_extractor.plan_embedded_images(content)
            return FileAnalysis(
                [
                    VisionCandidate(filename, image.number, "docx_image", DOCX_IMAGE_SCORE, f"embedded image {image.width}x{image.height}")
                    for image in plan.kept
                ]
            )
        return FileAnalysis([VisionCandidate(filename, 1, "image", STANDALONE_IMAGE_SCORE, "standalone image")])
    except Exception as exc:  # noqa: BLE001 - unsupported or corrupt files fail later, in extraction, with a recorded reason
        logger.info("vision planning skipped %s: %s: %s", filename, type(exc).__name__, exc)
        return FileAnalysis()


# --------------------------------------------------------------------------- #
# Global selection
# --------------------------------------------------------------------------- #
def _rank_key(candidate: VisionCandidate) -> tuple[float, str, int]:
    return (-candidate.score, candidate.source_doc, candidate.unit)


def select(
    candidates: Iterable[VisionCandidate],
    *,
    cap: int,
    strict: bool,
    per_doc: int = VISION_MAX_PAGES_PER_DOC,
) -> VisionSelection:
    """Keep the best candidates within ``cap``, spread across documents.

    With ``strict`` a set that still exceeds the cap after the per-document ceiling raises
    ``VisionPageCapExceeded``: tighten the thresholds above. Without it the best ``cap`` are kept, round-robin across
    documents, and everything left out is recorded in ``dropped``. Nothing is ever sent to OCR to make room.
    """
    qualified = sorted((c for c in candidates if c.score >= MIN_SELECT_SCORE), key=_rank_key)
    by_doc: dict[str, list[VisionCandidate]] = defaultdict(list)
    for candidate in qualified:
        by_doc[candidate.source_doc].append(candidate)

    dropped: list[tuple[VisionCandidate, str]] = []
    kept: dict[str, list[VisionCandidate]] = {}
    for doc in sorted(by_doc):
        kept[doc] = by_doc[doc][:per_doc]
        dropped += [(c, f"per-document ceiling of {per_doc} pages") for c in by_doc[doc][per_doc:]]

    total = sum(len(v) for v in kept.values())
    if total <= cap:
        chosen = [c for doc in sorted(kept) for c in kept[doc]]
    elif strict:
        raise VisionPageCapExceeded(total, cap)
    else:
        chosen = []
        depth = 0
        while len(chosen) < cap:
            this_round = sorted((v[depth] for v in kept.values() if len(v) > depth), key=_rank_key)
            if not this_round:
                break
            chosen += this_round[: cap - len(chosen)]
            depth += 1
        chosen_set = set(chosen)
        dropped += [(c, f"over the page cap of {cap}") for v in kept.values() for c in v if c not in chosen_set]

    chosen.sort(key=lambda c: (c.source_doc, c.unit))
    dropped.sort(key=lambda item: (item[0].source_doc, item[0].unit))
    return VisionSelection(chosen, dropped, [], cap, strict, len(qualified))


def plan_vision(
    files: Iterable[tuple[str, bytes]],
    *,
    strict: bool = True,
    cap: int | None = None,
    chart_dense_docs: frozenset[str] | set[str] = frozenset(),
) -> VisionSelection:
    """The global plan for one ingest run. Local only: no model, OCR or network call."""
    candidates: list[VisionCandidate] = []
    scanned: list[tuple[str, int]] = []
    for filename, content in files:
        analysis = analyze_file(filename, content, chart_dense=filename in chart_dense_docs)
        candidates += analysis.candidates
        scanned += [(filename, page) for page in analysis.scanned_pages]
    selection = select(candidates, cap=settings.MAX_VISION_PAGES if cap is None else cap, strict=strict)
    selection.scanned_to_ocr = scanned
    for candidate, why in selection.dropped:
        logger.warning("vision page dropped: %s %s (score %.1f, %s): %s", candidate.source_doc, candidate.label, candidate.score, candidate.reason, why)
    return selection


def load_chart_dense(manifest: Path | None = None) -> frozenset[str]:
    """Filenames the corpus manifest marks as holding tables or charts."""
    path = manifest or Path(__file__).resolve().parents[3] / "data" / "corpus" / "manifest.csv"
    if not path.exists():
        return frozenset()
    with path.open(encoding="utf-8", newline="") as handle:
        return frozenset(
            row["filename"] for row in csv.DictReader(handle) if row.get("has_tables_charts", "").strip().lower() == "yes"
        )


def select_vision_pages(
    corpus_files: Iterable[Path | tuple[str, bytes]],
    *,
    strict: bool = False,
    cap: int | None = None,
    chart_dense_docs: frozenset[str] | set[str] | None = None,
) -> list[tuple[str, int | str, float, str]]:
    """Dry run: (source_doc, page_or_image_id, score, reason) for everything that would go to vision.

    Makes no network call. ``strict=False`` by default so a listing is returned even when the plan is over the cap.
    """
    files = [(item.name, item.read_bytes()) if isinstance(item, Path) else item for item in corpus_files]
    dense = load_chart_dense() if chart_dense_docs is None else chart_dense_docs
    selection = plan_vision(files, strict=strict, cap=cap, chart_dense_docs=dense)
    return [(c.source_doc, c.label, c.score, c.reason) for c in selection.selected]
