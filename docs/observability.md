# Observability and the Query Stream Protocol

This document defines how the query pipeline reports progress and resource use. It implements D-31 (progress streamed as Server-Sent Events over a POST request), D-32 (token usage recorded inside the provider-client wrapper) and the Observability paragraph of the system architecture. It is the contract the frontend reads (FRONTEND-03, FRONTEND-04).

## Principles

1. **One mechanism.** Stage progress, per-call token usage, and timing all travel as events through an injected emitter. The pipeline never knows whether it is being streamed.
2. **Only progress streams, never the answer.** The answer is generated, structurally checked, verified, and passed through the output guardrail before anyone sees it. The stream carries stage progress and usage only. The answer text appears exactly once, in the terminal `result` event.
3. **Usage is recorded where calls are made.** `UsageTracker.record` (inside the Gemini client wrapper) forwards each recorded call to the emitter bound to the current request. A call that bypasses the wrapper bypasses usage reporting too, which is easy to spot in review.
4. **A disconnected client stops the pipeline.** Closing the response cancels the pipeline task, so no API quota is spent on an answer nobody will read.

## Emitters

| Emitter | Used by | Behaviour |
|---|---|---|
| `NullEmitter` | `POST /api/query`, the evaluation harness | Forwards nothing, but still records stage events and usage so the non-streaming response can report `stages` and `usage` |
| `QueueEmitter` | `POST /api/query/stream` | As `NullEmitter`, and also puts each event on an `asyncio.Queue` that the response generator drains. Safe to call from worker threads (usage is recorded on threads) |

A context manager around each pipeline stage emits `started`, then `completed` or `failed`, with the duration, so the events stay symmetric when a stage raises.

## Wire format

`POST /api/query/stream` takes the same JSON body as `POST /api/query` and responds with `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` (so reverse proxies do not buffer the stream). Each event is:

```
event: <name>
data: <one line of JSON>

```

The client reads it with a streaming `fetch`, not `EventSource` (which cannot POST).

### Events

| `event:` | When | `data` fields |
|---|---|---|
| `stage` | A stage starts, completes, or fails | `stage` (name), `status` (`started` / `completed` / `failed`), `duration_ms` (absent on `started`), `sub_question` (zero-based index; present only for the per-sub-question stages of a decomposed query) |
| `usage` | A model call is recorded | `stage` (model-call name, e.g. `query_generation`), `model`, `prompt_tokens`, `output_tokens`, `total_tokens` |
| `result` | Terminal, success | The full `QueryResponse` (same body as `POST /api/query`), whose `usage` field carries the total and the per-stage breakdown |
| `error` | Terminal, failure | `code` (`internal_error`), `message` (generic; never a stack trace) |

Exactly one terminal event (`result` or `error`) is sent, then the stream closes.

### Stage names, in pipeline order

`guardrail`, `greeting`, `guardrail_llm`, `greeting_llm`, `history`, `cache`, `decomposition`, `expansion`, `retrieval`, `fusion`, `sufficiency`, `web_search`, `rerank`, `generation`, `citations`, `verification`, `output_guardrail`.

Greetings, rejected input, and cache hits end early, so the stream stops after the stage that ended the query. The `expansion` through `rerank` stages repeat once per sub-question on a decomposed query, concurrently, each tagged with `sub_question`.

### Event order

Each stage emits `stage started`, then `stage completed` (or `failed`). A `usage` event arrives between a stage's `started` and `completed` whenever that stage made a model call, one per call.

**Normal query** (retrieved from the corpus), `event:` names in order:

`stage` pairs for `guardrail`, `greeting`, `guardrail_llm`, `greeting_llm`, `history`, `cache`, `decomposition`, `expansion`, `retrieval`, `fusion`, `sufficiency`, (`web_search` only when the corpus was judged insufficient), `rerank`, `generation`, `citations`, `verification`, `output_guardrail`, then one `result`. The model-calling stages (`guardrail_llm`, `history` when a rewrite is needed, `expansion`, `sufficiency` in the grey zone, `generation`, `verification`) each carry their `usage` events.

**Decomposed query:** identical up to and including `decomposition`. Then `expansion`, `retrieval`, `fusion`, `sufficiency`, (`web_search`), `rerank` run once per sub-question, concurrently, each event tagged `sub_question: <index>`. Events of different sub-questions interleave in any order; within one sub-question the order above holds. After the last sub-question's `rerank`, the answer stages run once for the whole question (`generation`, `citations`, `verification`, `output_guardrail` with no `sub_question` tag), then `result`. `result.decomposed` is `true` and `result.sub_questions` lists the sub-questions.

**Early exits:** a rejected input stops after its `guardrail` / `guardrail_llm` stage, a greeting after `greeting` / `greeting_llm`, and a cache hit after `cache`. Each is followed directly by `result`.

## Cancellation and failure

- **Client disconnect:** when the response generator closes, the pipeline task is cancelled and awaited under a shield so cancellation completes cleanly. No task outlives the request.
- **Pipeline exception:** logged with its stack trace on the server, then one `error` event is sent and the stream closes. The client never sees internals.
- **Validation errors** (bad session id, missing question) are rejected with an ordinary JSON error response *before* the stream opens.

## Error body (all endpoints)

```json
{"error": {"code": "bad_request", "message": "human-readable text"}}
```

`code` is one of `bad_request`, `not_found`, `conflict`, `payload_too_large`, `validation_error`, `internal_error`. Validation errors add `error.fields`, a list of `{field, message}`. Responses never contain stack traces.
