"""Ingest the document corpus, or print the vision plan for it without calling anything.

Run from backend/:
    python scripts/ingest_corpus.py --plan-vision          # local analysis only, no model call, exits afterwards
    python scripts/ingest_corpus.py                        # real ingestion; strict about the vision page cap
    python scripts/ingest_corpus.py --allow-truncation     # keep the best pages if the plan exceeds the cap
    python scripts/ingest_corpus.py --summary-json run.json   # also write the run's numbers to a file

The vision cap is MAX_VISION_PAGES. By default a plan over the cap is an error: tighten the thresholds in
app/ingestion/vision_select.py. --allow-truncation keeps the best pages, spread across documents, and reports the
rest as dropped.

By default the run stops at the first Gemini API error (after the key rotation's own retries). Without that, a quota
error inside a vision call would quietly fall back to OCR and the run would carry on, hiding the problem. Pass
--continue-on-api-error to get the old behaviour. To resume after a stop, simply run it again: vision responses and
embeddings are cached by content hash, so finished work is not paid for twice. OCR is not cached and is redone.

Exit codes: 0 done, 2 vision plan over the cap, 3 stopped on an API error.
"""

import argparse
import collections
import json
import logging
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.ingestion import embedder  # noqa: E402
from app.ingestion.extractors import vision  # noqa: E402
from app.ingestion.extractors.vision import VisionPageCapExceeded  # noqa: E402
from app.ingestion.pipeline import ingest_files  # noqa: E402
from app.ingestion.vision_select import load_chart_dense, plan_vision  # noqa: E402
from app.shared.session_store import PERSISTENT_SCOPE  # noqa: E402

DEFAULT_CORPUS = BACKEND.parent / "data" / "corpus" / "files"
DOCX_SHORT_CHUNK_TOKENS = 200


class ApiErrorStop(BaseException):
    """A BaseException so no `except Exception` fallback inside the pipeline can swallow it."""


class RunMonitor:
    """Counts the run's model calls and OCR time, collects fallback reasons, and can stop at the first API error."""

    def __init__(self, stop_on_error: bool) -> None:
        self.stop_on_error = stop_on_error
        self.vision_calls = 0
        self.vision_seconds = 0.0
        self.embed_calls = 0
        self.embed_texts = 0
        self.embed_seconds = 0.0
        self.ocr_seconds: list[float] = []
        self.fallback_reasons: list[str] = []
        self.api_error: str | None = None
        self._restores: list[tuple] = []
        self._handler: logging.Handler | None = None

    def _patch(self, target, name: str, value) -> None:
        had = name in vars(target)
        self._restores.append((target, name, had, vars(target).get(name)))
        setattr(target, name, value)

    def uninstall(self) -> None:
        for target, name, had, original in reversed(self._restores):
            if had:
                setattr(target, name, original)
            else:
                delattr(target, name)  # an instance attribute that was shadowing a method
        self._restores.clear()
        if self._handler is not None:
            logging.getLogger(vision.__name__).removeHandler(self._handler)
            self._handler = None

    def install(self) -> None:
        vision_client = vision._client
        embed_client = embedder._client
        self._patch(vision_client, "generate_vision", self._guard(vision_client.generate_vision, self._count_vision))
        self._patch(embed_client, "embed_batch", self._guard(embed_client.embed_batch, self._count_embed))
        self._patch(vision, "run_ocr", self._time_ocr(vision.run_ocr))
        self._handler = _FallbackHandler(self.fallback_reasons)
        logging.getLogger(vision.__name__).addHandler(self._handler)

    def _count_vision(self, seconds: float, args: tuple, kwargs: dict) -> None:
        self.vision_calls += 1
        self.vision_seconds += seconds

    def _count_embed(self, seconds: float, args: tuple, kwargs: dict) -> None:
        texts = args[0] if args else kwargs.get("texts", [])
        self.embed_calls += 1
        self.embed_texts += len(texts)
        self.embed_seconds += seconds

    def _guard(self, function, count):
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - recorded, then either stop the run or re-raise unchanged
                self.api_error = f"{type(exc).__name__}: {exc}"
                if self.stop_on_error:
                    raise ApiErrorStop(self.api_error) from exc
                raise
            finally:
                count(time.perf_counter() - started, args, kwargs)

        return wrapper

    def _time_ocr(self, function):
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.ocr_seconds.append(time.perf_counter() - started)

        return wrapper


class _FallbackHandler(logging.Handler):
    PREFIX = "vision -> OCR fallback"

    def __init__(self, sink: list[str]) -> None:
        super().__init__(level=logging.WARNING)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(self.PREFIX):
            reason = message.split("):", 1)[-1].strip()
            self.sink.append(reason.split(":", 1)[0] if reason.startswith(("vision call failed", "unusable")) else reason)


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


def store_report() -> dict:
    """What the persistent store and the keyword index hold right now."""
    import tiktoken

    from app.ingestion.indexer import get_keyword_index, get_vector_store

    encoding = tiktoken.get_encoding("cl100k_base")
    chunks = get_vector_store().all_chunks()
    by_suffix: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    docx_tokens: list[int] = []
    for chunk in chunks:
        suffix = chunk.source_doc.rsplit(".", 1)[-1].lower()
        by_suffix[suffix]["chunks"] += 1
        by_suffix[suffix][f"method:{chunk.extraction_method}"] += 1
        if suffix == "docx":
            docx_tokens.append(len(encoding.encode(chunk.text)))
    return {
        "chroma_count": get_vector_store().count(),
        "bm25_count": len(get_keyword_index()),
        "documents": len({c.source_doc for c in chunks}),
        "by_extraction_method": dict(collections.Counter(c.extraction_method for c in chunks)),
        "by_chunk_type": dict(collections.Counter(c.chunk_type for c in chunks)),
        "by_method_and_type": {f"{m}/{t}": n for (m, t), n in collections.Counter((c.extraction_method, c.chunk_type) for c in chunks).items()},
        "by_document_type": {suffix: dict(counter) for suffix, counter in sorted(by_suffix.items())},
        "embedding_models": dict(collections.Counter(c.embedding_model for c in chunks)),
        "docx_chunks": len(docx_tokens),
        "docx_tokens": (
            {"min": min(docx_tokens), "median": int(statistics.median(docx_tokens)), "max": max(docx_tokens),
             "under_200": sum(t < DOCX_SHORT_CHUNK_TOKENS for t in docx_tokens)}
            if docx_tokens else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--plan-vision", action="store_true", help="print the vision selection and exit")
    parser.add_argument("--allow-truncation", action="store_true", help="keep the top pages when over the cap")
    parser.add_argument("--continue-on-api-error", action="store_true", help="let vision errors fall back to OCR")
    parser.add_argument("--summary-json", type=Path, help="write the run's numbers to this file")
    args = parser.parse_args()

    paths = sorted(p for p in args.corpus_dir.iterdir() if p.is_file())
    files = [(p.name, p.read_bytes()) for p in paths]
    chart_dense = load_chart_dense()
    strict = not args.allow_truncation

    if args.plan_vision:
        try:
            selection = plan_vision(files, strict=strict, chart_dense_docs=chart_dense)
        except VisionPageCapExceeded as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Re-run with --allow-truncation to keep the best pages instead.", file=sys.stderr)
            return 2
        print_plan(selection, files, chart_dense)
        return 0

    monitor = RunMonitor(stop_on_error=not args.continue_on_api_error)
    monitor.install()
    before = store_report()
    started = time.perf_counter()
    summary: dict = {"ocr_engine": settings.OCR_ENGINE, "vision_cap": settings.MAX_VISION_PAGES, "files": len(files),
                     "store_before": {k: before[k] for k in ("chroma_count", "bm25_count")}}
    code = 0
    result = None
    try:
        result = ingest_files(files, corpus_scope=PERSISTENT_SCOPE, vision_strict=strict, chart_dense_docs=chart_dense)
    except VisionPageCapExceeded as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Re-run with --allow-truncation to keep the best pages instead.", file=sys.stderr)
        code = 2
        summary["stopped"] = "vision plan over the cap"
    except ApiErrorStop as exc:
        print(f"STOPPED at the first API error: {exc}", file=sys.stderr)
        print("Nothing was indexed after this point. Run the script again to resume: cached vision responses and "
              "embeddings are not paid for twice.", file=sys.stderr)
        code = 3
        summary["stopped"] = f"API error: {exc}"
    finally:
        monitor.uninstall()

    elapsed = time.perf_counter() - started
    tracker = vision._client.tracker
    entries = tracker._entries
    summary.update(
        elapsed_seconds=round(elapsed, 1),
        vision={"api_calls": monitor.vision_calls, "api_seconds": round(monitor.vision_seconds, 1),
                "tokens_by_stage": tracker.by_stage(),
                "prompt_tokens": sum(e.prompt_tokens or 0 for e in entries),
                "output_tokens": sum(e.output_tokens or 0 for e in entries),
                "fallback_to_ocr": len(monitor.fallback_reasons),
                "fallback_reasons": dict(collections.Counter(monitor.fallback_reasons))},
        embedding={"api_calls": monitor.embed_calls, "texts_embedded": monitor.embed_texts,
                   "api_seconds": round(monitor.embed_seconds, 1)},
        ocr={"calls": len(monitor.ocr_seconds), "seconds_total": round(sum(monitor.ocr_seconds), 1),
             "seconds_per_call_median": round(statistics.median(monitor.ocr_seconds), 1) if monitor.ocr_seconds else None,
             "seconds_per_call_max": round(max(monitor.ocr_seconds), 1) if monitor.ocr_seconds else None},
        api_error=monitor.api_error,
    )
    if result is not None:
        selection = result.vision
        print_plan(selection, files, chart_dense)
        index = result.index
        summary.update(
            chunks_produced=len(result.chunks),
            vision_units_planned=len(selection.selected),
            vision_dropped=len(selection.dropped),
            scanned_pages_to_ocr=len(selection.scanned_to_ocr),
            files_failed=[{"file": f.filename, "reason": f.reason} for f in result.failed],
            index={"total_indexed": index.total_indexed, "failed_chunks": index.failed_chunks,
                   "failure_reason": index.failure_reason, "by_extraction_method": index.by_extraction_method},
        )
        print(f"\nIngested {len(result.succeeded)} files, {len(result.failed)} failed, {len(result.chunks)} chunks "
              f"(vision cap {settings.MAX_VISION_PAGES}, OCR engine {settings.OCR_ENGINE}).")
        for failure in result.failed:
            print(f"  FAILED {failure.filename}: {failure.reason}")
    summary["store_after"] = store_report()

    print(f"\nElapsed {elapsed:.0f}s | vision API calls {monitor.vision_calls} | embedding API calls "
          f"{monitor.embed_calls} ({monitor.embed_texts} texts) | OCR calls {len(monitor.ocr_seconds)}, "
          f"{sum(monitor.ocr_seconds):.0f}s")
    print(f"Store: Chroma {summary['store_after']['chroma_count']} chunks, BM25 {summary['store_after']['bm25_count']}")
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"Summary written to {args.summary_json}")
    return code


if __name__ == "__main__":
    sys.exit(main())
