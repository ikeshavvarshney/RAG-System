"""Build the naive-baseline collection (D-39) by re-chunking production's stored text. Nothing is re-extracted.

Run from backend/:
    python scripts/ingest_naive.py --check      # is the stored order recoverable, how much text overlaps; no writes
    python scripts/ingest_naive.py --dry-run    # full build into a throwaway store with stub vectors; no API calls
    python scripts/ingest_naive.py              # build data/chroma_naive; rerun the same command to resume

Stored text chunks (text and OCR) are joined per source file and page in stored order, the 50-token overlaps the
production splitter left between neighbours are removed, and the result is re-split with the fixed-size splitter
(400-token windows, 50 overlap, no structure awareness). Vision pieces (charts, image captions) are copied with their
stored vectors. Every chunk is marked chunking="fixed". The production collection is only ever read.

The build is resumable: chunk ids are deterministic, finished batches are persisted as they complete, and embeddings
are cached by content, so after a quota error rerunning the same command embeds only what is missing.
"""

import argparse
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.core.gemini_client import GeminiClient  # noqa: E402
from app.ingestion import indexer, splitter  # noqa: E402
from app.shared.keyword_index import KeywordIndex  # noqa: E402
from app.shared.schemas.chunk import Chunk  # noqa: E402
from app.shared.session_store import PERSISTENT_SCOPE  # noqa: E402
from app.shared.vector_store import VectorStore, _to_chunk  # noqa: E402

DEFAULT_TARGET = BACKEND / "data" / "chroma_naive"
TEXT_METHODS = ("text", "ocr")
MIN_OVERLAP_CHARS = 20
MAX_OVERLAP_CHARS = 1500
MARK = {"chunking": "fixed"}


@dataclass
class Plan:
    windows: list[Chunk] = field(default_factory=list)
    reused: list[tuple[Chunk, list[float]]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def read_production() -> tuple[list[tuple[Chunk, list[float]]], int]:
    store = VectorStore(path=settings.CHROMA_PATH)
    try:
        raw = store._collection.get(include=["documents", "metadatas", "embeddings"])
        pairs = [
            (_to_chunk(cid, doc, meta), [float(x) for x in vec])
            for cid, doc, meta, vec in zip(raw["ids"], raw["documents"], raw["metadatas"], raw["embeddings"])
        ]
        return pairs, store.count()
    finally:
        store.close()


def production_count() -> int:
    store = VectorStore(path=settings.CHROMA_PATH)
    try:
        return store.count()
    finally:
        store.close()


def overlap_length(previous: str, following: str) -> int:
    for k in range(min(len(previous), len(following), MAX_OVERLAP_CHARS), MIN_OVERLAP_CHARS - 1, -1):
        if previous.endswith(following[:k]):
            return k
    return 0


def is_rechunked(chunk: Chunk) -> bool:
    return chunk.chunk_type == "text" and chunk.extraction_method in TEXT_METHODS


def build_plan(pairs: list[tuple[Chunk, list[float]]]) -> Plan:
    groups: dict[tuple, list[Chunk]] = {}
    plan = Plan()
    for chunk, vector in pairs:
        if is_rechunked(chunk):
            groups.setdefault((chunk.source_doc, chunk.page, chunk.extraction_method), []).append(chunk)
        else:
            plan.reused.append((chunk, vector))

    splitter.FIXED_SIZE_MODE = True
    pair_total = pair_overlapping = duplicate_chars = 0
    by_kind = {"paged": [0, 0], "unpaged": [0, 0]}
    source_chars = merged_chars = 0
    for (source_doc, page, method), chunks in groups.items():
        text = chunks[0].text
        source_chars += sum(len(c.text) for c in chunks)
        kind = "paged" if page is not None else "unpaged"
        for previous, following in zip(chunks, chunks[1:]):
            k = overlap_length(text, following.text)
            pair_total += 1
            by_kind[kind][0] += 1
            if k:
                pair_overlapping += 1
                by_kind[kind][1] += 1
                duplicate_chars += k
            text = text + ("" if k else "\n") + following.text[k:]
        merged_chars += len(text)
        metadata = {
            "source_doc": source_doc,
            "page": page,
            "location": chunks[0].location,
            "extraction_method": method,
            "corpus_scope": PERSISTENT_SCOPE,
        }
        for piece in splitter.split(text, metadata):
            plan.windows.append(Chunk(**{**piece, "corpus_scope": PERSISTENT_SCOPE}))

    kinds: dict[str, int] = {}
    for chunk in [c for c, _ in plan.reused] + plan.windows:
        key = f"{chunk.chunk_type}/{chunk.extraction_method}"
        kinds[key] = kinds.get(key, 0) + 1
    plan.stats = {
        "production_chunks": len(pairs),
        "text_groups": len(groups),
        "adjacent_pairs": pair_total,
        "pairs_with_overlap": pair_overlapping,
        "pairs_by_kind": {k: {"pairs": v[0], "overlapping": v[1]} for k, v in by_kind.items()},
        "production_text_chars": source_chars,
        "duplicate_chars_removed": duplicate_chars,
        "merged_text_chars": merged_chars,
        "naive_window_chars": sum(len(c.text) for c in plan.windows),
        "naive_chunks": len(plan.windows) + len(plan.reused),
        "naive_chunks_by_type": kinds,
        "windows": len(plan.windows),
        "reused_vision_pieces": len(plan.reused),
    }
    return plan


def mark_chunking(store: VectorStore) -> int:
    raw = store._collection.get(include=["metadatas"])
    ids = [i for i, m in zip(raw["ids"], raw["metadatas"]) if m.get("chunking") != MARK["chunking"]]
    metas = [{**m, **MARK} for m in raw["metadatas"] if m.get("chunking") != MARK["chunking"]]
    for start in range(0, len(ids), 500):
        store._collection.update(ids=ids[start : start + 500], metadatas=metas[start : start + 500])
    return len(ids)


def build(plan: Plan, store: VectorStore) -> dict:
    existing = set(store._collection.get(include=[])["ids"])
    keywords = KeywordIndex(store)
    reused = [(c, v) for c, v in plan.reused if c.chunk_id not in existing]
    if reused:
        store.upsert(reused)
    todo = [c for c in plan.windows if c.chunk_id not in existing]
    result = indexer.index_chunks(todo, vector_store=store, keyword_index=keywords)
    marked = mark_chunking(store)
    keywords.rebuild()
    return {
        "already_present": len(existing),
        "vision_pieces_copied": len(reused),
        "windows_to_embed": len(todo),
        "windows_embedded": result.total_indexed,
        "failed": result.failed_chunks,
        "failure_reason": result.failure_reason,
        "store_total": store.count(),
        "marked_fixed": marked,
    }


def print_stats(stats: dict) -> None:
    for key, value in stats.items():
        print(f"{key}: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = args.target.resolve()
    if target == Path(settings.CHROMA_PATH).resolve():
        print("ERROR: the target is the production collection", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    pairs, before = read_production()
    plan = build_plan(pairs)
    print_stats(plan.stats)
    code = 0

    if not args.check:
        def no_api(*_a, **_k):
            raise AssertionError("an API call was attempted during a dry run")

        scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True) if args.dry_run else None
        if args.dry_run:
            GeminiClient.embed_batch = no_api
            GeminiClient.generate_vision = no_api
            dimension = len(plan.reused[0][1])
            embedded: list[str] = []
            quota = {"batches_left": 2}

            def stub_embed(chunks):
                if quota["batches_left"] == 0:
                    raise RuntimeError("simulated quota error")
                quota["batches_left"] -= 1
                embedded.extend(c.chunk_id for c in chunks)
                return [(c, [0.0] * dimension) for c in chunks]

            indexer.embed_chunks = stub_embed
        store = VectorStore(path=scratch.name if scratch else str(target))
        try:
            outcome = build(plan, store)
            if args.dry_run:
                print("pass 1 (quota error after 2 batches):", outcome)
                quota["batches_left"] = 10**6
                outcome = build(plan, store)
                print(f"pass 2 resumed; texts embedded in total: {len(embedded)}, distinct: {len(set(embedded))}")
            print("build outcome:", outcome)
            if outcome["failure_reason"]:
                print(f"STOPPED: {outcome['failure_reason']}\nRerun the same command to resume.", file=sys.stderr)
                code = 3
        finally:
            store.close()
            if scratch:
                scratch.cleanup()
        print("api calls: 0 (stubbed)" if args.dry_run else "api calls: embedding only")

    after = production_count()
    print(f"production collection chunks before={before} after={after} untouched={before == after}")
    return code if before == after else 4


if __name__ == "__main__":
    sys.exit(main())
