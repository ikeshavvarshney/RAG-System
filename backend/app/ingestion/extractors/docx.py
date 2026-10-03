"""DOCX extraction: sectioned body text, tables as markdown, and embedded images (D-18).

The body becomes ONE text piece in document order: each heading is written as a markdown `## ` line, consecutive
paragraphs sit under it, and each table is one markdown table block. The structure-aware splitter then cuts it at
headings into target-size chunks and never splits a table. Embedded images go through the same vision-then-OCR path
as standalone images.
"""

import hashlib
import io
import logging
from dataclasses import dataclass, field

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image

from app.core.config import settings
from app.ingestion.extractors import vision

logger = logging.getLogger(__name__)

# Location carried by body chunks that sit before any heading.
BODY_LOCATION = "document"

# Embedded images below either bound are treated as decorative (bullets, rules, logos' slivers) and skipped.
MIN_IMAGE_BYTES = 4_096
MIN_IMAGE_SIDE_PX = 64

# Formats the vision model accepts as they are; anything else is re-encoded to PNG first.
_NATIVE_MIME_TYPES = {"image/png", "image/jpeg"}


@dataclass(frozen=True)
class EmbeddedImage:
    """One kept image: ``number`` is its 1-based position among all images in the document."""

    number: int
    mime_type: str
    width: int
    height: int
    size_bytes: int
    data: bytes = field(repr=False)

    @property
    def location(self) -> str:
        return f"embedded_image_{self.number}"


@dataclass
class ImagePlan:
    kept: list[EmbeddedImage] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (number, reason)


# --------------------------------------------------------------------------- #
# Body text
# --------------------------------------------------------------------------- #
def _is_heading(paragraph: Paragraph) -> bool:
    try:
        name = (paragraph.style.name or "").lower()
    except Exception:  # noqa: BLE001 - a missing style is just "not a heading"
        return False
    return name == "title" or name.startswith("heading")


def _cell_text(cell) -> str:
    return " ".join(cell.text.split()).replace("|", "\\|")


def _table_markdown(table: Table) -> str:
    """One markdown table for the whole DOCX table, merged cells collapsed."""
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            # A horizontally merged cell is reported once per grid column: keep its text in the first column and
            # leave the rest empty so the columns stay aligned.
            cells.append("" if cell._tc is previous else _cell_text(cell))
            previous = cell._tc
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _body_text(document) -> str:
    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            blocks.append(f"## {text}" if _is_heading(paragraph) else text)
        elif child.tag == qn("w:tbl"):
            markdown = _table_markdown(Table(child, document))
            if markdown:
                blocks.append(markdown)
    return "\n\n".join(blocks)


def extract_text_pieces(content: bytes) -> list[dict]:
    """The body of a DOCX as extraction pieces, with no model or OCR call."""
    return _text_pieces(docx.Document(io.BytesIO(content)))


def _text_pieces(document) -> list[dict]:
    text = _body_text(document)
    if not text:
        return []
    return [{"page": None, "location": BODY_LOCATION, "text": text, "extraction_method": "text"}]


# --------------------------------------------------------------------------- #
# Embedded images
# --------------------------------------------------------------------------- #
def _image_parts(document) -> list:
    """Image parts in document order, each once, from the package's related parts."""
    related = document.part.related_parts
    ordered = list(dict.fromkeys(document.element.body.xpath(".//a:blip/@r:embed")))
    ordered += [rid for rid in related if rid not in ordered]  # images reached only by legacy markup
    return [related[rid] for rid in ordered if rid in related and related[rid].content_type.startswith("image/")]


def _as_vision_input(part) -> tuple[bytes, str, int, int] | str:
    """(bytes, mime, width, height) ready for the vision path, or a reason string when unusable."""
    blob = part.blob
    try:
        with Image.open(io.BytesIO(blob)) as image:
            width, height = image.size
            mime = part.content_type
            if mime not in _NATIVE_MIME_TYPES:
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="PNG")
                blob, mime = buffer.getvalue(), "image/png"
    except Exception as exc:  # noqa: BLE001 - EMF/WMF and corrupt images are skipped, not fatal
        return f"unreadable image ({part.content_type}): {type(exc).__name__}"
    return blob, mime, width, height


def _plan_images(document) -> ImagePlan:
    plan = ImagePlan()
    seen: set[str] = set()
    for number, part in enumerate(_image_parts(document), start=1):
        digest = hashlib.sha256(part.blob).hexdigest()
        if digest in seen:
            plan.skipped.append((number, "duplicate of an earlier image"))
            continue
        seen.add(digest)
        if len(part.blob) < MIN_IMAGE_BYTES:
            plan.skipped.append((number, f"decorative: {len(part.blob)} bytes < {MIN_IMAGE_BYTES}"))
            continue
        prepared = _as_vision_input(part)
        if isinstance(prepared, str):
            plan.skipped.append((number, prepared))
            continue
        blob, mime, width, height = prepared
        if min(width, height) < MIN_IMAGE_SIDE_PX:
            plan.skipped.append((number, f"decorative: {width}x{height}px, shorter side < {MIN_IMAGE_SIDE_PX}"))
            continue
        plan.kept.append(EmbeddedImage(number, mime, width, height, len(blob), blob))
    return plan


def plan_embedded_images(content: bytes) -> ImagePlan:
    """Which embedded images would be kept, and why the rest are skipped. Calls nothing."""
    return _plan_images(docx.Document(io.BytesIO(content)))


def images_for_vision(content: bytes) -> list[EmbeddedImage]:
    """The images that WOULD be sent to vision: kept images within the MAX_VISION_PAGES budget. Calls nothing.

    Kept images beyond the budget still get extracted, by OCR.
    """
    return plan_embedded_images(content).kept[: settings.MAX_VISION_PAGES]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def extract(content: bytes, filename: str, vision_units: set[int] | None = None):
    """Extract body text, tables and embedded images from a DOCX file.

    ``vision_units`` holds the image numbers the global plan sent to vision. Called without one, the file plans for
    itself. A kept image the plan left out is not extracted.
    """
    document = docx.Document(io.BytesIO(content))
    pieces = _text_pieces(document)

    plan = _plan_images(document)
    for number, reason in plan.skipped:
        logger.info("%s: embedded image %d skipped (%s)", filename, number, reason)
    if vision_units is None:
        vision_units = {i.number for i in plan.kept[: settings.MAX_VISION_PAGES]}
    chosen = [image for image in plan.kept if image.number in vision_units]
    for image in plan.kept:
        if image.number not in vision_units:
            logger.warning("%s: %s left out of the vision plan; not extracted", filename, image.location)
    plan.kept = chosen
    if plan.kept:
        # The same vision-then-OCR path as standalone images. One image that fails both must not lose the text.
        pieces.extend(
            vision.extract_pages(
                [
                    vision.VisionPage(image.data, page=None, location=image.location, mime_type=image.mime_type)
                    for image in plan.kept
                ],
                skip_failures=True,
            )
        )
    return pieces
