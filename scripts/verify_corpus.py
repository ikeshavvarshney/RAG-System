#!/usr/bin/env python3
"""Measures the downloaded corpus and rewrites the manifest's `pages` column
with true page counts. Run after scripts/fetch_corpus.sh, before
scripts/validate_corpus.py.

    pip install pypdf
    python scripts/verify_corpus.py

This script measures and writes back; it does not enforce anything. The
sourcing targets the corpus was assembled against (50 files, 15 MB per file,
250 MB total, 250-400 total pages) are printed as advisory notes, since they
describe how the document list was chosen rather than what the project
requires. The binding constraints live in scripts/validate_corpus.py.

DOCX page counts cannot be computed reliably from Python (pagination is Word's
layout engine), so those values are carried through from the manifest, where
they were read off the Office web viewer. To measure one:
    libreoffice --headless --convert-to pdf <file>   # then count the PDF pages
"""
from __future__ import annotations

import csv
import os
from collections import Counter

CORPUS = os.path.join("data", "corpus")
DOCUMENTS = os.path.join(CORPUS, "files")
MANIFEST = os.path.join(CORPUS, "manifest.csv")

# Sourcing targets. Reported as notes, never fatal.
TARGET_FILES = 50
TARGET_FILE_BYTES = 15 * 1024 * 1024
TARGET_TOTAL_BYTES = 250 * 1024 * 1024
TARGET_PAGE_RANGE = (250, 400)
TARGET_MAX_PAGES_PER_FILE = 40


def main() -> int:
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        rows = list(reader)

    problems: list[str] = []
    notes: list[str] = []
    changed: list[str] = []
    total_pages = 0
    total_bytes = 0
    measured_files = 0

    for row in rows:
        name = row["filename"]
        path = os.path.join(DOCUMENTS, name)
        if not os.path.exists(path):
            problems.append(f"MISSING {name}")
            total_pages += int(row["pages"] or 0)
            continue

        measured_files += 1
        size = os.path.getsize(path)
        total_bytes += size
        if size > TARGET_FILE_BYTES:
            notes.append(f"over 15MB target: {name} {size / 1e6:.1f}MB")

        if row["doc_type"] == "image":
            pages = 1
        elif row["doc_type"] == "pdf":
            try:
                from pypdf import PdfReader

                pages = len(PdfReader(path).pages)
            except ImportError:
                problems.append("pypdf not installed: pip install pypdf")
                return 1
            except Exception as exc:
                problems.append(f"UNREADABLE {name}: {exc}")
                continue
        else:
            pages = int(row["pages"])

        if pages > TARGET_MAX_PAGES_PER_FILE:
            notes.append(f"over 40-page target: {name} {pages}")
        if str(pages) != row["pages"]:
            changed.append(f"{name}: {row['pages']} -> {pages}")
        row["pages"] = str(pages)
        total_pages += pages

    if len(rows) != TARGET_FILES:
        notes.append(f"file count {len(rows)}, sourcing target {TARGET_FILES}")
    if not TARGET_PAGE_RANGE[0] <= total_pages <= TARGET_PAGE_RANGE[1]:
        notes.append(
            f"total pages {total_pages} outside the "
            f"{TARGET_PAGE_RANGE[0]}-{TARGET_PAGE_RANGE[1]} target: "
            "see data/corpus/HANDOFF.md for pre-verified substitute documents"
        )
    if total_bytes > TARGET_TOTAL_BYTES:
        notes.append(f"total size {total_bytes / 1e6:.0f}MB over the 250MB target")

    if measured_files == 0:
        # Nothing fetched yet: one line beats 50 identical MISSING lines.
        problems[:] = [
            f"MISSING all {len(rows)} documents: run ./scripts/fetch_corpus.sh first"
        ]

    if changed:
        with open(MANIFEST, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    types = Counter(r["doc_type"] for r in rows)
    print(
        f"rows={len(rows)} measured={measured_files} pages={total_pages} "
        f"size={total_bytes / 1e6:.1f}MB types={dict(types)}"
    )
    if changed:
        print(f"rewrote {len(changed)} page counts in {MANIFEST}:")
        for line in changed:
            print(f"  {line}")
    else:
        print("page counts already correct; manifest unchanged")
    for line in notes:
        print(f"note: {line}")
    if problems:
        print("\n".join(problems))
    print("Now run: python scripts/validate_corpus.py --strict --write-summary")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
