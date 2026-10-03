import logging

from app.ingestion import embedder
from app.query import llm, rerank

logger = logging.getLogger(__name__)


def warm_up_clients() -> None:
    """Each step is independent: a failing client build must not stop the reranker from loading."""
    for name, step in (("generation client", llm.warm_up), ("embedder", embedder.warm_up), ("reranker", rerank.warm_up)):
        try:
            step()
        except Exception:  # noqa: BLE001 - warm-up is an optimisation; the first use loads lazily
            logger.warning("%s warm-up failed; it will load on first use", name, exc_info=True)
