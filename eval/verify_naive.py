"""Shows that --preset naive retrieves from the naive collection with no rerank, expansion or decomposition.

Runs the retrieval stages only (up to the context that would go to the generator), so no answer is generated.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402  (sets cwd, sys.path and loads the env)

IDS = ("Q02", "Q20", "Q32")


@contextmanager
def no_stage(name, sub_question=None):
    yield


async def check(prod_ids: set[str]) -> int:
    from app.core.config import settings
    from app.core.logging import configure_logging
    from app.ingestion.indexer import get_keyword_index, get_vector_store
    from app.query.decomposition import decompose, looks_compound
    from app.query.pipeline import retrieve_and_rank
    from app.shared.session_store import PERSISTENT_SCOPE
    from app.shared.vector_store import VectorStore

    configure_logging()
    questions = [q for q in run_eval.load_questions(run_eval.DEFAULT_REFERENCE) if q["id"] in IDS]
    ok = True
    for q in questions:
        sub_questions = await decompose(q["question"])
        ranked = await retrieve_and_rank(
            q["question"], no_stage, corpus_scope=PERSISTENT_SCOPE,
            vector_store=get_vector_store(), keyword_index=get_keyword_index(),
        )
        store = get_vector_store()
        passages = [c for c in ranked.context.passages if c.source == "corpus"]
        marks = store._collection.get(ids=[c.chunk_id for c in passages], include=["metadatas"])
        found = {i: m.get("chunking") for i, m in zip(marks["ids"], marks["metadatas"])}
        in_naive = sum(1 for c in passages if c.chunk_id in found)
        fixed = sum(1 for v in found.values() if v == "fixed")
        window_ids = [c.chunk_id for c in passages if c.chunk_id not in prod_ids]
        keyword_only = sum(1 for c in passages if {p.retriever for p in c.provenance} == {"keyword"})
        print(f"\n{q['id']}: looks_compound={looks_compound(q['question'])} sub_questions={len(sub_questions)}")
        print(f"  expanded_queries={len(ranked.expanded_queries)} reranked={ranked.context.reranked}")
        print(f"  dense_weight={settings.FUSION_DENSE_WEIGHT} fused={len(ranked.fused)} vector_hits={len(ranked.retrieval.vector_hits)}")
        print(f"  final corpus passages={len(passages)} (web passages: {len(ranked.context.passages) - len(passages)}): in naive collection={in_naive}, chunking=fixed={fixed}, keyword-only={keyword_only}")
        print(f"  passages not present in the production collection (fixed-size windows): {len(window_ids)}")
        for c in passages[:3]:
            print(f"    {c.chunk_id} chunking={found.get(c.chunk_id)} {c.metadata.get('source_doc')} p{c.metadata.get('page')} len={len(c.text)}")
        ok &= (
            not sub_questions and len(ranked.expanded_queries) == 1 and not ranked.context.reranked
            and in_naive == fixed == len(passages) and keyword_only == 0
        )
    print(f"\nPASS: {ok}")
    return 0 if ok else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from app.core.config import settings
    from app.shared.vector_store import VectorStore

    production = VectorStore(path=settings.CHROMA_PATH)
    prod_ids = {c.chunk_id for c in production.all_chunks()}
    print(f"production collection read before the preset is applied: {len(prod_ids)} chunks")
    production.close()
    overrides = run_eval.resolve_overrides("naive", None, [])
    with run_eval.applied(overrides):
        return asyncio.run(check(prod_ids))


if __name__ == "__main__":
    sys.exit(main())
