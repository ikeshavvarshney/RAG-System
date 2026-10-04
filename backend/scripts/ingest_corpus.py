"""Ingest the document corpus, or print the vision plan for it without calling anything.

Run from backend/:
    python scripts/ingest_corpus.py --plan-vision          # local analysis only, no model call, exits afterwards
    python scripts/ingest_corpus.py                        # real ingestion; strict about the vision page cap
    python scripts/ingest_corpus.py --allow-truncation     # keep the best pages if the plan exceeds the cap

The vision cap is MAX_VISION_PAGES. By default a plan over the cap is an error: tighten the thresholds in
app/ingestion/vision_select.py. --allow-truncation keeps the best pages, spread across documents, and reports the
rest as dropped.
"""

import argparse
import collections
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.ingestion.extractors.vision import VisionPageCapExceeded  # noqa: E402
from app.ingestion.pipeline import ingest_files  # noqa: E402
from app.ingestion.vision_select import load_chart_dense, plan_vision  # noqa: E402
from app.shared.session_store import PERSISTENT_SCOPE  # noqa: E402

DEFAULT_CORPUS = BACKEND.parent / "data" / "corpus" / "files"


def print_plan(selection, files, chart_dense) -> None:
    cap = selection.cap
    per_doc = collections.Counter(c.source_doc for c in selection.selected)
    kinds = collections.Counter(c.kind for c in selection.selected)
    names = {name for name, _ in files}
    present_dense = chart_dense & names
    covered_dense = sorted(d for d in per_doc if d in present_dense)
    print(f"Vision plan: {len(selection.selected)} of {cap} pages selected "
          f"({selection.qualified} candidates had a strong signal; {len(selection.dropped)} dropped)")
    print(f"  by kind: {dict(kinds)}")
    print(f"  documents with at least one selected page: {len(per_doc)} of {len(files)}")
    print(f"  chart-dense documents covered: {len(covered_dense)} of {len(present_dense)}")
    print(f"  scanned pages routed to OCR (not vision): {len(selection.scanned_to_ocr)} "
          f"in {len({d for d, _ in selection.scanned_to_ocr})} documents")
    fallback = [c for c in selection.selected if c.fallback]
    print(f"  fallback pages (chart-dense documents with no normally scored page): {len(fallback)}")
    for c in fallback:
        print(f"    {c.source_doc} page {c.label} ({c.reason})")
    uncovered = sorted(d for d in present_dense if d not in per_doc)
    print(f"  chart-dense documents still uncovered: {len(uncovered)}")
    for doc in uncovered:
        print(f"    {doc}: {selection.notes.get(doc) or 'all of its candidate pages were dropped (see Dropped)'}")
    if selection.selected:
        lowest = min(selection.selected, key=lambda c: (c.score, c.source_doc, c.unit))
        print(f"  lowest-scoring selected page: {lowest.source_doc} {lowest.label} "
              f"score {lowest.score:g} ({lowest.reason})")
    print("\nSelected per document:")
    for doc, count in sorted(per_doc.items()):
        marker = "*" if doc in chart_dense else " "
        print(f"  {marker} {doc}: {count}")
    if selection.dropped:
        print("\nDropped:")
        for candidate, why in selection.dropped:
            print(f"  {candidate.source_doc} {candidate.label} score {candidate.score:g}: {why}")
    print("\nSelected pages:")
    for c in selection.selected:
        print(f"  {c.source_doc}\t{c.label}\t{c.score:g}\t{c.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--plan-vision", action="store_true", help="print the vision selection and exit")
    parser.add_argument("--allow-truncation", action="store_true", help="keep the top pages when over the cap")
    args = parser.parse_args()

    paths = sorted(p for p in args.corpus_dir.iterdir() if p.is_file())
    files = [(p.name, p.read_bytes()) for p in paths]
    chart_dense = load_chart_dense()
    strict = not args.allow_truncation

    try:
        if args.plan_vision:
            selection = plan_vision(files, strict=strict, chart_dense_docs=chart_dense)
            print_plan(selection, files, chart_dense)
            return 0
        result = ingest_files(
            files, corpus_scope=PERSISTENT_SCOPE, vision_strict=strict, chart_dense_docs=chart_dense
        )
    except VisionPageCapExceeded as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Re-run with --allow-truncation to keep the best pages instead.", file=sys.stderr)
        return 2

    print_plan(result.vision, files, chart_dense)
    print(f"\nIngested {len(result.succeeded)} files, {len(result.failed)} failed, {len(result.chunks)} chunks "
          f"(vision cap {settings.MAX_VISION_PAGES}).")
    for failure in result.failed:
        print(f"  FAILED {failure.filename}: {failure.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
