"""The two named LCEL chains (D-46). Each wraps the existing pipeline function; the HTTP handlers only
validate input, pick a chain, invoke it and serialize the result."""

from typing import Any

from langchain_core.runnables import RunnableLambda

from app.core.usage import usage_sink
from app.ingestion.pipeline import IngestResult, ingest_files
from app.query.emitter import NullEmitter
from app.query.pipeline import QueryResult, run_query


async def _run_query_stage(inputs: dict[str, Any]) -> QueryResult:
    """inputs: question, session_id, and optionally an emitter (a NullEmitter when absent)."""
    emitter = inputs.get("emitter") or NullEmitter()
    token = usage_sink.set(emitter.usage)
    try:
        return await run_query(inputs["question"], inputs["session_id"], on_event=emitter.stage)
    finally:
        usage_sink.reset(token)


def _ingest_stage(inputs: dict[str, Any]) -> IngestResult:
    """inputs: files [(filename, bytes)], corpus_scope, and optionally vector_store and keyword_index."""
    return ingest_files(
        inputs["files"],
        corpus_scope=inputs["corpus_scope"],
        vector_store=inputs.get("vector_store"),
        keyword_index=inputs.get("keyword_index"),
    )


query_chain = RunnableLambda(_run_query_stage).with_config(run_name="query_chain")
ingestion_chain = RunnableLambda(_ingest_stage).with_config(run_name="ingestion_chain")
