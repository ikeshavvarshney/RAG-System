"""Proves bypass_cache skips the answer cache lookup and write. Uses a temporary CACHE_PATH."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402  (sets cwd, sys.path and loads the env)

QUESTION_ID = "Q01"


async def run(question: str, bypass: bool) -> dict:
    from app.query.pipeline import run_query

    stages: list[str] = []

    def on_event(event) -> None:
        if event.status == "completed":
            stages.append(f"{event.stage}:{event.duration_ms:.0f}ms")

    began = time.perf_counter()
    result = await run_query(question, f"verify-{uuid.uuid4().hex}", on_event=on_event, bypass_cache=bypass)
    return {
        "terminated_at": result.terminated_at,
        "seconds": round(time.perf_counter() - began, 2),
        "stages": stages,
        "answer": (result.answer or "")[:70],
    }


def show(label: str, info: dict) -> None:
    print(f"{label}: terminated_at={info['terminated_at']} seconds={info['seconds']}")
    print(f"    stages: {', '.join(info['stages'])}")


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="verify_bypass_cache_")
    os.environ["CACHE_PATH"] = tmp
    from app.query.cache import get_answer_cache

    cache = get_answer_cache()
    question = next(q["question"] for q in run_eval.load_questions(run_eval.DEFAULT_REFERENCE) if q["id"] == QUESTION_ID)
    print(f"temporary cache: {tmp}\nquestion: {question}\n")

    before = cache.count()
    first = await run(question, bypass=True)
    second = await run(question, bypass=True)
    after = cache.count()
    show("(a) bypass run 1", first)
    show("(a) bypass run 2", second)
    full = all(r["terminated_at"] == "retrieved" and any(s.startswith("retrieval:") for s in r["stages"]) for r in (first, second))
    print(f"(a) both ran the full pipeline: {full}")
    print(f"(b) cache count before={before} after={after} unchanged={before == after}\n")

    normal = await run(question, bypass=False)
    show("(c) bypass_cache=False", normal)
    print(f"(c) cache count after normal run={cache.count()} (was {after})\n")

    again = await run(question, bypass=True)
    show("(d) bypass_cache=True with a matching entry in the cache", again)
    hit = await run(question, bypass=False)
    show("(d) control, bypass_cache=False", hit)
    ok = full and before == after and cache.count() == after + 1 and again["terminated_at"] != "cache_hit"
    print(f"\nPASS: {ok}")
    cache.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
