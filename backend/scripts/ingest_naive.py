"""Build the naive-baseline collection (D-39): the corpus re-chunked with the fixed-size splitter, in its own directory.

Run from backend/:
    python scripts/ingest_naive.py --dry-run     # extract + split into a throwaway store, count chunks, embed nothing
    python scripts/ingest_naive.py               # build data/chroma_naive

The production collection is never opened for writing: the target must differ from CHROMA_PATH, and the script
reports the production chunk count before and after. Vision is capped at zero uncached calls (--max-units 0 in
ingest_corpus terms), so only responses already in the vision cache are reused and nothing is paid for twice.
"""

import argparse
import importlib.util
import logging
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.ingestion import indexer, splitter  # noqa: E402
from app.ingestion.pipeline import ingest_files  # noqa: E402
from app.ingestion.vision_select import load_chart_dense  # noqa: E402
from app.shared.keyword_index import KeywordIndex  # noqa: E402
from app.shared.session_store import PERSISTENT_SCOPE  # noqa: E402
from app.shared.vector_store import VectorStore  # noqa: E402

_spec = importlib.util.spec_from_file_location("ingest_corpus", BACKEND / "scripts" / "ingest_corpus.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

DEFAULT_TARGET = BACKEND / "data" / "chroma_naive"


def production_count() -> int:
    import chromadb

    return chromadb.PersistentClient(path=settings.CHROMA_PATH).get_collection("chunks").count()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--corpus-dir", type=Path, default=base.DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = args.target.resolve()
    if target == Path(settings.CHROMA_PATH).resolve():
        print("ERROR: the target is the production collection", file=sys.stderr)
        return 2
    if not args.dry_run and target.exists() and any(target.iterdir()):
        print(f"ERROR: {target} already has data; delete it first to rebuild", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    before = production_count()
    files = [(p.name, p.read_bytes()) for p in sorted(p for p in args.corpus_dir.iterdir() if p.is_file())]
    splitter.FIXED_SIZE_MODE = True
    monitor = base.RunMonitor(stop_on_error=True, max_units=0)
    monitor.install()

    scratch = tempfile.TemporaryDirectory() if args.dry_run else None
    if args.dry_run:
        indexer.embed_chunks = lambda chunks: [(c, [0.0] * 8) for c in chunks]
    store = VectorStore(path=scratch.name if scratch else str(target))
    code = 0
    try:
        result = ingest_files(
            files,
            corpus_scope=PERSISTENT_SCOPE,
            vision_strict=False,
            chart_dense_docs=load_chart_dense(),
            vector_store=store,
            keyword_index=KeywordIndex(store),
        )
        counts: dict = {}
        for chunk in result.chunks:
            key = f"{chunk['chunk_type']}/{chunk['extraction_method']}"
            counts[key] = counts.get(key, 0) + 1
        print(f"{'DRY RUN: ' if args.dry_run else ''}chunks by type/method: {counts}; total {len(result.chunks)}")
        print(f"files failed: {[f.filename for f in result.failed]}")
        print(f"embedding calls: {monitor.embed_calls}, texts embedded: {monitor.embed_texts}")
        print(f"vision API calls: {monitor.vision_calls}, fallbacks to OCR: {len(monitor.fallback_reasons)}")
    except BaseException as exc:  # noqa: BLE001 - the monitor's stop signals are BaseExceptions
        print(f"STOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 3
    finally:
        monitor.uninstall()
        store.close()
        if scratch:
            scratch.cleanup()

    after = production_count()
    print(f"production collection chunks before={before} after={after} untouched={before == after}")
    return code if before == after else 4


if __name__ == "__main__":
    sys.exit(main())
