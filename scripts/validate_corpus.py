#!/usr/bin/env python3
"""Validates data/corpus against the collection rules in data/corpus/README.md.

This is the project gate. The constraints enforced here are the ones stated in
the requirements and methodology docs (D-15 to D-18): 20-50 documents, at least
eight with genuine tables or charts, at least one scanned/image-only PDF, at
least one DOCX with embedded images, all three media families present, ASCII
hyphenated filenames, and a complete manifest row per document.

    python scripts/validate_corpus.py [--strict] [--write-summary]

    --strict          exit 1 if any error was reported (default: exit 0)
    --write-summary   regenerate data/corpus/MANIFEST.md

Page counts and file sizes are read from disk where possible. PDF text-layer
detection requires pypdf; without it the scanned-PDF check is reported as
skipped rather than passed.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import string
import sys
import zipfile
from collections import Counter

CORPUS = os.path.join("data", "corpus")
# The documents live in a git-ignored subdirectory; the manifest beside it is
# the tracked artefact and is what makes the corpus reproducible.
DOCUMENTS = os.path.join(CORPUS, "files")
MANIFEST = os.path.join(CORPUS, "manifest.csv")
SUMMARY = os.path.join(CORPUS, "MANIFEST.md")

MIN_DOCUMENTS = 20
MAX_DOCUMENTS = 50
MIN_CHART_DENSE = 8
WARN_BYTES = 25 * 1024 * 1024
ERROR_BYTES = 50 * 1024 * 1024

EXTENSIONS = {
    "pdf": {".pdf"},
    "docx": {".docx"},
    "image": {".jpg", ".jpeg", ".png"},
}
ALL_EXTENSIONS = {ext for exts in EXTENSIONS.values() for ext in exts}
REQUIRED_COLUMNS = [
    "filename",
    "doc_type",
    "title",
    "source_url",
    "has_tables_charts",
    "pages",
    "license_note",
]

FILENAME_ALLOWED = set(string.ascii_lowercase + string.digits + "-.")
FILENAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+$")

errors: list[str] = []
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def pdf_page_count_and_text(path: str):
    """Returns (pages, has_text_layer). Either may be None if unreadable."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, None
    try:
        reader = PdfReader(path)
    except Exception as exc:
        error(f"UNREADABLE {os.path.basename(path)}: {exc}")
        return None, None
    text = ""
    for page in reader.pages:
        try:
            text += page.extract_text() or ""
        except Exception:
            pass
        if len(text.strip()) > 200:
            break
    return len(reader.pages), len(text.strip()) > 200


def docx_has_embedded_images(path: str):
    """Returns True/False, or None if the file cannot be opened as a zip."""
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.startswith("word/media/") for n in zf.namelist())
    except Exception as exc:
        error(f"UNREADABLE {os.path.basename(path)}: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on any error")
    parser.add_argument(
        "--write-summary", action="store_true", help="regenerate MANIFEST.md"
    )
    args = parser.parse_args()

    if not os.path.exists(MANIFEST):
        print(f"missing manifest: {MANIFEST}")
        return 1
    os.makedirs(DOCUMENTS, exist_ok=True)

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing_columns:
        error(f"MANIFEST COLUMNS missing {', '.join(missing_columns)}")
        print("\n".join(errors))
        return 1

    # ---- per-row checks -------------------------------------------------
    seen: set[str] = set()
    total_bytes = 0
    total_pages = 0
    on_disk = 0
    scanned_pdfs: list[str] = []
    docx_with_images: list[str] = []
    text_check_skipped = False

    for row in rows:
        name = row["filename"]

        if not name:
            error("EMPTY FILENAME in manifest")
            continue
        if name in seen:
            error(f"DUPLICATE ROW {name}")
        seen.add(name)

        for column in REQUIRED_COLUMNS:
            if not (row.get(column) or "").strip():
                error(f"EMPTY COLUMN {name}: {column}")

        if set(name) - FILENAME_ALLOWED or not FILENAME_PATTERN.match(name):
            error(f"BAD FILENAME {name}: lowercase ASCII, hyphenated, no spaces")

        doc_type = row["doc_type"]
        extension = os.path.splitext(name)[1].lower()
        type_ok = True
        if doc_type not in EXTENSIONS:
            error(f"BAD DOC_TYPE {name}: {doc_type!r}")
            type_ok = False
        elif extension not in EXTENSIONS[doc_type]:
            error(f"TYPE MISMATCH {name}: doc_type={doc_type} extension={extension}")
            type_ok = False
        elif extension not in ALL_EXTENSIONS:
            error(f"UNSUPPORTED EXTENSION {name}")
            type_ok = False

        if row["has_tables_charts"] not in ("yes", "no"):
            error(f"BAD has_tables_charts {name}: {row['has_tables_charts']!r}")

        try:
            manifest_pages = int(row["pages"])
            if manifest_pages < 1:
                error(f"BAD PAGES {name}: {row['pages']!r}")
        except ValueError:
            error(f"BAD PAGES {name}: {row['pages']!r}")
            manifest_pages = 0

        path = os.path.join(DOCUMENTS, name)
        if not os.path.exists(path):
            error(f"MISSING {name}")
            total_pages += manifest_pages
            continue

        on_disk += 1
        size = os.path.getsize(path)
        total_bytes += size
        if size > ERROR_BYTES:
            error(f"OVER 50MB {name}: {size / 1e6:.1f}MB")
        elif size > WARN_BYTES:
            warn(f"over 25MB {name}: {size / 1e6:.1f}MB")

        pages = manifest_pages
        if not type_ok:
            # doc_type and extension disagree; probing the content would only
            # report a second error about the same underlying mistake.
            total_pages += pages
            continue
        if doc_type == "image":
            pages = 1
            if manifest_pages != 1:
                error(f"PAGES {name}: images must be 1, manifest says {manifest_pages}")
        elif doc_type == "pdf":
            measured, has_text = pdf_page_count_and_text(path)
            if measured is None:
                text_check_skipped = True
            else:
                pages = measured
                if measured != manifest_pages:
                    error(f"PAGES {name}: manifest {manifest_pages}, actual {measured}")
                if has_text is False:
                    scanned_pdfs.append(name)
        elif doc_type == "docx":
            # Word pagination cannot be computed from Python; the manifest value
            # stands. scripts/verify_corpus.py documents how to measure one.
            if docx_has_embedded_images(path):
                docx_with_images.append(name)

        total_pages += pages

    # ---- corpus-wide checks ---------------------------------------------
    if not MIN_DOCUMENTS <= len(rows) <= MAX_DOCUMENTS:
        error(f"DOCUMENT COUNT {len(rows)} outside {MIN_DOCUMENTS}-{MAX_DOCUMENTS}")

    chart_dense = sum(1 for r in rows if r["has_tables_charts"] == "yes")
    if chart_dense < MIN_CHART_DENSE:
        error(f"CHART-DENSE {chart_dense} < {MIN_CHART_DENSE} (D-16)")

    types = Counter(r["doc_type"] for r in rows)
    for family in ("pdf", "docx", "image"):
        if not types[family]:
            error(f"MISSING MEDIA FAMILY {family}")

    if on_disk == 0:
        # Nothing fetched yet: one line beats 50 identical MISSING lines.
        errors[:] = [e for e in errors if not e.startswith("MISSING ")]
        error(
            f"MISSING all {len(rows)} documents: run ./scripts/fetch_corpus.sh, "
            "then python scripts/verify_corpus.py"
        )
    else:
        if text_check_skipped:
            warn("scanned-PDF check skipped: pypdf not installed (pip install pypdf)")
        elif not scanned_pdfs:
            error("NO SCANNED/IMAGE-ONLY PDF (D-17)")
        if not docx_with_images and types["docx"]:
            error("NO DOCX WITH EMBEDDED IMAGES (D-18)")

        stray = [
            f
            for f in sorted(os.listdir(DOCUMENTS))
            if f not in seen and os.path.isfile(os.path.join(DOCUMENTS, f))
        ]
        for name in stray:
            error(f"UNLISTED FILE {name}: on disk but not in manifest")

    # ---- report ----------------------------------------------------------
    print(
        f"documents={len(rows)} on_disk={on_disk} pages={total_pages} "
        f"size={total_bytes / 1e6:.1f}MB types={dict(types)} "
        f"chart_dense={chart_dense} ({chart_dense / len(rows) * 100:.0f}%)"
    )
    if scanned_pdfs:
        print(f"scanned/image-only PDFs: {', '.join(scanned_pdfs)}")
    if docx_with_images:
        print(f"DOCX with embedded images: {', '.join(docx_with_images)}")
    for message in warnings:
        print(f"warning: {message}")
    print("\n".join(errors) if errors else "All corpus checks passed.")

    if args.write_summary:
        write_summary(rows, types, chart_dense, total_pages, total_bytes, on_disk)
        print(f"wrote {SUMMARY}")

    return 1 if (errors and args.strict) else 0


def write_summary(rows, types, chart_dense, total_pages, total_bytes, on_disk) -> None:
    lines = [
        "# Corpus Manifest",
        "",
        "Generated by `python scripts/validate_corpus.py --write-summary`.",
        "Do not edit by hand; edit `manifest.csv` and regenerate.",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
        f"| documents | {len(rows)} |",
        f"| present on disk | {on_disk} |",
        f"| pdf | {types['pdf']} |",
        f"| docx | {types['docx']} |",
        f"| image | {types['image']} |",
        f"| with tables or charts | {chart_dense} ({chart_dense / len(rows) * 100:.0f}%) |",
        f"| total pages | {total_pages} |",
        f"| total size | {total_bytes / 1e6:.1f} MB |",
        "",
        "## Documents",
        "",
        "| filename | type | pages | tables/charts | title | licence |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        title = row["title"].replace("|", "\\|")
        licence = row["license_note"].replace("|", "\\|")
        lines.append(
            f"| `{row['filename']}` | {row['doc_type']} | {row['pages']} | "
            f"{row['has_tables_charts']} | {title} | {licence} |"
        )
    lines.append("")
    with open(SUMMARY, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
