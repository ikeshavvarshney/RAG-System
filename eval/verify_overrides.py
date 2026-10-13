"""Checks that bad config keys are rejected and that overrides are always restored."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402  (sets cwd, sys.path and loads the env)


def snapshot() -> dict:
    from app.core.config import settings

    return {key: getattr(settings, key) for key in run_eval.ALLOWED_KEYS}


def stores_unbuilt() -> bool:
    import app.ingestion.indexer as indexer

    return indexer._vector_store is None and indexer._keyword_index is None


def main() -> int:
    before = snapshot()
    results = {}

    sys.argv = ["run_eval", "--set", "NOPE=1", "--run-name", "verify_overrides_unused"]
    results["bad key rejected with exit code 2"] = run_eval.main() == 2
    results["settings unchanged after bad key"] = snapshot() == before

    overrides = run_eval.resolve_overrides("naive", None, ["RRF_K=7"])
    try:
        with run_eval.applied(overrides):
            during = snapshot()
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    results["overrides were live inside the block"] = (
        during["FUSION_DENSE_WEIGHT"] == 1.0 and during["RRF_K"] == 7 and during["RERANK_ENABLED"] is False
        and during["CHROMA_PATH"].endswith("chroma_naive")
    )
    results["settings restored after Ctrl+C"] = snapshot() == before
    results["store singletons reset after CHROMA_PATH override"] = stores_unbuilt()

    sys.argv = ["run_eval", "--set", "FUSION_DENSE_WEIGHT=0.0", "--ids", "NO_SUCH_ID", "--run-name", "verify_overrides_unused"]
    code = run_eval.main()
    results["error path inside the run returns 2"] = code == 2
    results["settings restored after error path"] = snapshot() == before

    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print("before:", before)
    print("after: ", snapshot())
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
