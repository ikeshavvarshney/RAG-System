"""Calibrate the sufficiency thresholds against the real indexed corpus.

Runs expand -> retrieve -> fuse -> sufficiency on each question in the questions file, appends one CSV row per
question as soon as it completes, and writes a markdown summary with a threshold suggestion. Nothing in the
config is changed.

    cd backend
    .venv/Scripts/python scripts/calibrate_sufficiency.py [--resume] [--max-questions N] [--no-llm]
"""

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)  # settings reads .env relative to the cwd
sys.path.insert(0, str(BACKEND))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.config import settings  # noqa: E402
from app.ingestion.indexer import get_keyword_index, get_vector_store  # noqa: E402
from app.query.expansion import expand_query  # noqa: E402
from app.query.fusion import fuse  # noqa: E402
from app.query.retrieval import retrieve  # noqa: E402
from app.query.sufficiency import assess  # noqa: E402
from app.shared.session_store import PERSISTENT_SCOPE  # noqa: E402

FIELDS = [
    "id", "label", "question", "top_dense", "top_dense_original", "mean_top3_dense", "top_keyword",
    "candidates", "queries", "verdict", "expected", "matched", "method", "reason", "expected_chunk_found",
]


class Degraded(Exception):
    pass


def _interleaved(questions: list[dict]) -> list[dict]:
    inside = [q for q in questions if q["label"] == "in_corpus"]
    outside = [q for q in questions if q["label"] != "in_corpus"]
    ordered: list[dict] = []
    for i in range(max(len(inside), len(outside))):
        ordered += inside[i : i + 1] + outside[i : i + 1]
    return ordered


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _append_row(path: Path, row: dict) -> None:
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if fresh:
            writer.writeheader()
        writer.writerow(row)
        fh.flush()


async def _run_question(item: dict) -> dict:
    question = item["question"]
    queries = await expand_query(question)
    if len(queries) < 2:
        raise Degraded("query expansion returned only the original question (likely quota or an LLM failure)")

    retrieval = await retrieve(
        question,
        queries,
        vector_store=get_vector_store(),
        keyword_index=get_keyword_index(),
        corpus_scope=PERSISTENT_SCOPE,
        top_k=settings.RETRIEVAL_TOP_K,
    )
    fused = fuse(retrieval, dense_weight=settings.FUSION_DENSE_WEIGHT, k=settings.RRF_K)
    result = await assess(question, fused)
    if result.method == "fallback":
        raise Degraded(f"sufficiency LLM verdict failed ({result.reason})")

    original = [
        p.score for c in fused for p in c.provenance
        if p.retriever == "vector" and p.query == question and p.score is not None
    ]
    expected = item["label"] == "in_corpus"
    expected_chunk = item.get("expected_chunk_id")
    return {
        "id": item["id"],
        "label": item["label"],
        "question": question,
        "top_dense": _fmt(result.scores.top_dense),
        "top_dense_original": _fmt(max(original) if original else None),
        "mean_top3_dense": _fmt(result.scores.mean_top3_dense),
        "top_keyword": _fmt(result.scores.top_keyword, 2),
        "candidates": len(fused),
        "queries": len(queries),
        "verdict": result.sufficient,
        "expected": expected,
        "matched": result.sufficient == expected,
        "method": result.method,
        "reason": result.reason,
        "expected_chunk_found": (expected_chunk in {c.chunk_id for c in fused}) if expected_chunk else "",
    }


def _fmt(value: float | None, digits: int = 4) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def suggest_thresholds(inside: list[float], outside: list[float], margin: float) -> tuple[float, float, str]:
    """Returns (low, high, shape). Below low only out-of-corpus questions were seen, above high only in-corpus."""
    lowest_in, highest_out = min(inside), max(outside)
    if lowest_in > highest_out:
        step = min(margin, (lowest_in - highest_out) / 4)
        return highest_out + step, lowest_in - step, "clean gap between the labels"
    return lowest_in - margin, highest_out + margin, "labels overlap"


def _trimmed(values: list[float], drop_high: bool) -> list[float]:
    ordered = sorted(values)
    return ordered[:-1] if drop_high else ordered[1:]


def _resolution(rows: list[dict], low: float, high: float) -> dict[str, int]:
    tally = {"auto_sufficient_right": 0, "auto_sufficient_wrong": 0, "auto_insufficient_right": 0,
             "auto_insufficient_wrong": 0, "grey": 0}
    for row in rows:
        score, expected = float(row["top_dense"]), row["expected"] == "True"
        if score >= high:
            tally["auto_sufficient_right" if expected else "auto_sufficient_wrong"] += 1
        elif score < low:
            tally["auto_insufficient_wrong" if expected else "auto_insufficient_right"] += 1
        else:
            tally["grey"] += 1
    return tally


def _spread(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"min {min(values):.3f} / median {statistics.median(values):.3f} / max {max(values):.3f}"


def _project(row: dict, low: float, high: float) -> tuple[bool | None, str]:
    """The verdict this row would get under the given thresholds, from evidence already recorded."""
    score = float(row["top_dense"])
    if score >= high:
        return True, "score"
    if score < low:
        return False, "score"
    if row["method"] == "llm":
        return row["verdict"] == "True", "llm (recorded)"
    return None, "pending LLM"


def build_summary(rows: list[dict], margin: float) -> tuple[str, str]:
    scored = [r for r in rows if r["top_dense"]]
    inside = [float(r["top_dense"]) for r in scored if r["label"] == "in_corpus"]
    outside = [float(r["top_dense"]) for r in scored if r["label"] != "in_corpus"]
    low_now, high_now = settings.SUFFICIENCY_LOW_THRESHOLD, settings.SUFFICIENCY_HIGH_THRESHOLD
    projected = {r["id"]: _project(r, low_now, high_now) for r in scored}
    decided = [r for r in scored if projected[r["id"]][0] is not None]
    matched = sum(projected[r["id"]][0] == (r["label"] == "in_corpus") for r in decided)
    pending = [r["id"] for r in scored if projected[r["id"]][0] is None]
    recorded = [r for r in rows if r["matched"] in ("True", "False")]
    by_method: dict[str, int] = {}
    for r in rows:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1

    lines = [
        "# Sufficiency calibration",
        "",
        f"{len(rows)} questions ({len(inside)} in-corpus, {len(outside)} out-of-corpus). "
        f"As recorded when run: **{sum(r['matched'] == 'True' for r in recorded)}/{len(recorded)}** matched "
        f"({', '.join(f'{k}={v}' for k, v in sorted(by_method.items()))}).",
        "",
        f"Projected at the current thresholds (low={low_now}, high={high_now}), reusing recorded LLM verdicts: "
        f"**{matched}/{len(decided)}** matched, {len(pending)} pending an LLM call"
        + (f" ({', '.join(pending)})." if pending else "."),
        "",
        "## Top dense similarity (best over all expanded queries)",
        "",
        f"- in-corpus: {_spread(inside)}",
        f"- out-of-corpus: {_spread(outside)}",
        "",
        "## Per question",
        "",
        "| id | label | top dense | original only | recorded | at current thresholds | match | reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        reason = r["reason"].replace("|", "/")[:110]
        verdict, path = projected.get(r["id"], (None, "n/a"))
        outcome = "pending" if verdict is None else ("yes" if verdict == (r["label"] == "in_corpus") else "NO")
        lines.append(
            f"| {r['id']} | {r['label']} | {r['top_dense']} | {r['top_dense_original']} | "
            f"{r['verdict']} ({r['method']}) | {verdict} ({path}) | {outcome} | {reason} |"
        )

    suggestion = "Not enough data: need at least one scored question of each label."
    if inside and outside:
        low, high, shape = suggest_thresholds(inside, outside, margin)
        candidates = {"suggested": (low, high)}
        if len(inside) > 1 and len(outside) > 1:
            robust_low, robust_high, _ = suggest_thresholds(_trimmed(inside, False), _trimmed(outside, True), margin)
            candidates["robust (ignores the single worst outlier per label)"] = (robust_low, robust_high)
        lines += ["", f"## Threshold suggestion ({shape}, margin {margin})", "",
                  "| candidate | low | high | auto sufficient (right/wrong) | auto insufficient (right/wrong) | grey (LLM) |",
                  "|---|---|---|---|---|---|"]
        candidates["current config"] = (settings.SUFFICIENCY_LOW_THRESHOLD, settings.SUFFICIENCY_HIGH_THRESHOLD)
        for name, (lo, hi) in candidates.items():
            t = _resolution(scored, lo, hi)
            lines.append(
                f"| {name} | {lo:.3f} | {hi:.3f} | {t['auto_sufficient_right']}/{t['auto_sufficient_wrong']} | "
                f"{t['auto_insufficient_right']}/{t['auto_insufficient_wrong']} | {t['grey']} |"
            )
        suggestion = (
            f"SUFFICIENCY_LOW_THRESHOLD={low:.3f}  SUFFICIENCY_HIGH_THRESHOLD={high:.3f}  ({shape})"
        )
        lines += ["", f"Suggested: `{suggestion}`", "",
                  "Sample sizes are small: treat the margin as a floor, not a guarantee."]

    misses = [r for r in decided if projected[r["id"]][0] != (r["label"] == "in_corpus")]
    if misses:
        lines += ["", "## Mismatches at the current thresholds", ""] + [
            f"- {r['id']} ({r['label']}, top dense {r['top_dense']}, {projected[r['id']][1]}): {r['question']}"
            for r in misses
        ]
    not_found = [r for r in rows if r["expected_chunk_found"] == "False"]
    if not_found:
        lines += ["", "## In-corpus questions whose expected chunk was not retrieved", ""] + [
            f"- {r['id']}: {r['question']}" for r in not_found
        ]
    return "\n".join(lines) + "\n", suggestion


async def _run(args: argparse.Namespace) -> int:
    questions = json.loads(args.questions.read_text(encoding="utf-8"))["questions"]
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        sys.exit("question ids must be unique")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, summary_path = args.out_dir / "sufficiency_calibration.csv", args.out_dir / "sufficiency_calibration.md"
    if csv_path.exists() and not (args.resume or args.overwrite):
        sys.exit(f"{csv_path} exists: pass --resume to continue it or --overwrite to start over")
    if args.overwrite and not args.resume:
        csv_path.unlink(missing_ok=True)
    if args.no_llm:
        settings.SUFFICIENCY_LLM_ENABLED = False

    done = {r["id"] for r in _read_rows(csv_path)}
    pending = [q for q in _interleaved(questions) if q["id"] not in done]
    if args.max_questions is not None:
        pending = pending[: args.max_questions]
    print(f"{len(done)} already in the CSV, running {len(pending)} of {len(questions)} questions")

    status = 0
    for item in pending:
        try:
            row = await _run_question(item)
        except Degraded as exc:
            print(f"STOPPED at {item['id']}: {exc}. Re-run with --resume once it recovers.")
            status = 2
            break
        except Exception as exc:  # noqa: BLE001 - embedding or store failure: keep what is already written
            print(f"STOPPED at {item['id']}: {type(exc).__name__}: {exc}")
            status = 1
            break
        _append_row(csv_path, row)
        print(f"{row['id']}: top_dense={row['top_dense']} verdict={row['verdict']} method={row['method']} "
              f"matched={row['matched']}")

    rows = _read_rows(csv_path)
    if rows:
        summary, suggestion = build_summary(rows, args.margin)
        summary_path.write_text(summary, encoding="utf-8")
        print(f"\nwrote {csv_path}\nwrote {summary_path}\n\nSuggested thresholds (config NOT changed):\n  {suggestion}")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=BACKEND / "scripts" / "calibration_questions.json")
    parser.add_argument("--out-dir", type=Path, default=BACKEND / "data" / "calibration")
    parser.add_argument("--resume", action="store_true", help="skip questions already in the CSV")
    parser.add_argument("--overwrite", action="store_true", help="discard an existing CSV and start over")
    parser.add_argument("--max-questions", type=int, default=None, help="cap the questions run this time")
    parser.add_argument("--no-llm", action="store_true", help="score gates only; grey zone follows the lean")
    parser.add_argument("--margin", type=float, default=0.01, help="safety margin around the observed scores")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
