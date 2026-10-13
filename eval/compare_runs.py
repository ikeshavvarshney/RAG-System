"""Per-metric deltas between two scored runs, using only questions answered from the corpus in both."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
SOURCES = ("corpus", "web", "mixed", "none")


def load(arg: str) -> dict:
    path = Path(arg)
    if not path.exists():
        path = RESULTS_DIR / f"{arg}.scored.json"
    return json.loads(path.read_text(encoding="utf-8"))


def corpus_rows(run: dict) -> dict[str, dict]:
    return {r["id"]: r for r in run["per_question"] if r["status"] == "ok" and r["answer_source"] == "corpus"}


def split_line(run: dict) -> str:
    split = run["split"]
    return ", ".join(f"{s}={split['counts'][s]} ({split['percent'][s]}%)" for s in SOURCES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="scored file path or run name")
    parser.add_argument("candidate", help="scored file path or run name")
    args = parser.parse_args()

    a, b = load(args.baseline), load(args.candidate)
    rows_a, rows_b = corpus_rows(a), corpus_rows(b)
    common = sorted(set(rows_a) & set(rows_b))

    print(f"baseline  {a['run_name']}: {split_line(a)}")
    print(f"candidate {b['run_name']}: {split_line(b)}")
    print(f"\nquestions corpus-answered in BOTH runs: {len(common)}")
    if not common:
        print("no common corpus-answered questions; no deltas to report")
        return 1
    print(f"{'metric':20}{'baseline':>10}{'candidate':>11}{'delta':>9}{'n':>5}")
    for metric in METRICS:
        pairs = [
            (rows_a[i]["scores"][metric], rows_b[i]["scores"][metric])
            for i in common
            if rows_a[i]["scores"].get(metric) is not None and rows_b[i]["scores"].get(metric) is not None
        ]
        if not pairs:
            print(f"{metric:20}{'n/a':>10}{'n/a':>11}{'n/a':>9}{0:>5}")
            continue
        mean_a = sum(p[0] for p in pairs) / len(pairs)
        mean_b = sum(p[1] for p in pairs) / len(pairs)
        print(f"{metric:20}{mean_a:>10.4f}{mean_b:>11.4f}{mean_b - mean_a:>+9.4f}{len(pairs):>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
