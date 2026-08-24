# 7. Design Decisions

A record of the significant technical decisions taken during design, with the reasoning behind each and the alternatives considered. Decisions are grouped by area and identified for reference from the codebase and other documents.

---

## 7.1 Model and Service Selection

**D-01 — Google Gemini as the primary model provider.** The larger model handles final answer generation; the faster model handles vision extraction, query expansion, decomposition, conversational resolution, guardrails, and sufficiency assessment.

*Reasoning:* the deciding factor was operational rather than qualitative. Several providers offer comparable models on comparable free tiers. Serving generation, vision, and embeddings from one provider means one credential pool to rotate and one rate limit to reason about, rather than three interacting limits whose behaviour under load is difficult to predict. Reserving the larger model for generation alone materially reduces end-to-end latency, since the query pipeline performs several model calls in sequence.

**D-02 — Gemini `text-embedding-004` for embeddings.** Same provider and credential surface as the language models.

*Alternative considered:* larger open-weight embedding models achieve better retrieval benchmark results. Self-hosting one requires GPU memory beyond what is available. Recorded as a limitation in the research methodology rather than presented as a neutral choice.

**D-03 — Chroma as the vector store.** Local, disk-persisted, no external infrastructure.

*Reasoning:* a 20-50 document corpus does not justify a hosted vector database. Two practical advantages follow for this project specifically: the corpus travels with the repository so both members work against identical data, and RQ2's requirement for two parallel indexes is straightforward with local collections but awkward against a hosted free tier's storage allowance.

**D-04 — `rank_bm25` for sparse retrieval.** In-process, pure Python.

*Reasoning:* a dedicated search engine would be disproportionate infrastructure at this corpus size. The index is held in memory and rebuilt from persisted passage text at startup, which is effectively instantaneous here.

**D-06 — Tavily for web search fallback.** Straightforward API, free tier, results structured for programmatic use.

**D-08 — Multi-key rotation across API providers.** A utility distributing requests round-robin across a pool of credentials per provider.

*Reasoning:* free-tier rate limits are the binding operational constraint on the project, particularly during the evaluation week when the question set is executed repeatedly. Built in Week 1 because retrofitting it after the pipeline exists would require modifying every call site.

---

## 7.2 Data Model

**D-09, D-10 — Passage schema.** Core fields covering identity, content, and provenance, plus two fields serving purposes beyond ordinary operation.

`extraction_method` records whether a passage was produced by vision extraction, OCR, or direct text extraction. RQ2 compares the first two, and this field is what makes the comparison possible. It must be recorded accurately from the first ingestion run, including on fallback paths — a passage produced by OCR fallback but labelled as vision-extracted would silently corrupt the study's headline result.

`corpus_scope` separates the persistent corpus from ad-hoc session uploads within a single store, supporting scoped retrieval without maintaining parallel infrastructure.

**D-11 — Citations modelled as a discriminated union.** Corpus-sourced citations carry document and page; web-sourced citations carry a URL and title.

*Reasoning:* the two have genuinely different identity, and both the groundedness filter and the interface branch on the distinction. Representing it structurally rather than as nullable fields on a single type makes the branch explicit and checkable.

**D-12 — Structure-aware chunking targeting 300-500 tokens.** Segmentation at headings, paragraph breaks, and table blocks in preference to fixed offsets.

*Reasoning:* fixed-size splitting routinely divides a passage mid-sentence or mid-table, degrading both retrieval and the readability of quoted context. Tables are never split; an oversized intact table is more useful than two fragments. Token counts are measured with a tokeniser rather than approximated from character counts, since the two diverge substantially on dense text.

---

## 7.3 Structure and Process

**D-13, D-14 — Monorepo with pipeline modules inside the backend package.** Ingestion and query modules are subpackages of the backend rather than top-level projects; ownership follows the module boundary.

*Reasoning:* simplest arrangement for two people over eight weeks with no deployment split. The module boundary and the ownership boundary coincide, so the two members rarely edit the same files.

**D-15, D-16 — Corpus composition.** Mixed general domain, with a minimum of eight documents containing genuine tables, charts, or figures, enforced by an automated validator.

*Reasoning:* the domain choice trades question depth for collection speed and licence clarity, accepted knowingly. The chart-density floor is a methodological requirement rather than a preference: RQ2 measures a difference on visual content, and a corpus containing little visual content cannot produce a measurable difference regardless of how the extraction methods perform. Enforced automatically because a shortfall discovered during the evaluation week cannot be remedied then.

---

## 7.4 Ingestion Behaviour

**D-17 — Scanned PDFs are detected and processed by OCR immediately.** Pages whose extracted character count falls below a threshold are rasterised and passed to OCR, decided per page rather than per document.

*Reasoning:* documents mixing digital and scanned pages are common, and a per-document decision handles them incorrectly in both directions.

**D-18 — DOCX ingestion extracts both body text and embedded images.** Embedded images are processed by OCR.

*Reasoning:* a substantial amount of tabular content in DOCX files appears as pasted images rather than as native tables. Note also that table-cell text is not present in the document's paragraph collection and must be walked separately, or it is silently lost.

**D-19 — Per-file error isolation.** An unsupported, corrupt, or unreadable file is skipped with a recorded error; the remainder of the batch proceeds.

*Reasoning:* ingesting a corpus of forty documents must not abort on the twelfth. Failures are reported per file with their cause.

**D-26 — PyMuPDF for PDF handling.** Text extraction and page rasterisation from one library.

*Reasoning:* the scanned-document path requires rendering pages to images. Alternative PDF libraries provide extraction but not rendering, which would mean maintaining two libraries for one file type.

**D-27 — PaddleOCR with PP-Structure as the primary OCR engine; Tesseract as fallback.**

*Reasoning:* driven by RQ2 more than by general accuracy. PP-Structure performs table structure recognition, recovering rows and columns rather than emitting words in reading order. With a structure-blind baseline, much of what RQ2 would measure is simply structured versus unstructured output — an unsurprising result for vision extraction that says little about the interesting question. A structure-aware baseline makes the comparison a genuine test of whether vision-language understanding adds value beyond accurate transcription. Tesseract is retained so that an unavailable primary engine does not block ingestion.

**D-28 — Fast model for vision extraction, subject to empirical verification.** Both available models are compared against a sample of real corpus tables before the full corpus is processed; the selection is then held fixed.

*Reasoning:* vision extraction is the heaviest ingestion cost against the free tier, and the faster model is substantially cheaper. Whether it is sufficient is a question about actual output quality on actual documents, so it is settled by measurement rather than assumption. Holding the selection fixed thereafter is required for RQ2's baseline consistency.

---

## 7.5 Query Behaviour

**D-21 — The answer cache is keyed by document scope and by resolved question, and records the documents each answer cited.**

*Reasoning:* three consequences follow, each addressing a distinct correctness failure. Session isolation prevents an answer derived from one user's uploads being served to another. Recording cited documents allows cache invalidation when a document is deleted — without which the system would continue serving an answer citing a document that no longer exists. And keying on the resolved rather than the literal question prevents a follow-up such as *"what about 2024?"* from colliding across unrelated conversations.

Because a cache hit bypasses the entire pipeline, no downstream stage can catch an incorrect hit. The similarity threshold is therefore set conservatively.

**D-22 — The keyword index is rebuilt after any corpus change.**

*Reasoning:* the BM25 implementation maintains its own in-memory copy of passage text and provides no delete operation. A document removed from the vector store remains fully searchable through the sparse path — and can still be retrieved and quoted into an answer — until the index is rebuilt. At this corpus size a complete rebuild is effectively instantaneous, so no incremental approach is warranted.

**D-23 — Document deletion reaches four locations.** Vector store, keyword index, answer cache, and extraction cache.

*Reasoning:* omitting any one leaves deleted content reachable by some path. Deletion is scoped — a session may delete only its own uploads — and operates on documents rather than passages, matching the user's mental model.

**D-24 — Conversational context is bounded to the last 2-3 turns, and follow-up questions are resolved into self-contained form before any downstream stage.**

*Reasoning:* retrieval, expansion, decomposition, and caching are all incapable of handling an unresolved pronoun, so resolution must precede them. The window is bounded deliberately: full conversation history costs tokens on every call and actively degrades resolution quality once the topic changes. Questions already self-contained pass through unmodified — an over-eager rewriter that attaches prior context to an unrelated new question is worse than no history at all.

**D-25 — Reranking uses a local cross-encoder rather than a hosted reranking API.**

*Reasoning:* a 22-million-parameter cross-encoder executes in tens of milliseconds on CPU. A hosted alternative would add a network round trip to every query, a third credential pool, and a third rate limit, in exchange for quality the local model broadly matches at this corpus size. A larger cross-encoder remains available as a drop-in replacement should reranking prove to be the accuracy bottleneck during evaluation.

**D-29 — Embeddings are cached by content hash.**

*Reasoning:* the ablation studies re-run the same question set across multiple configurations, producing thousands of redundant embedding requests for identical text. The cache addresses the resulting quota pressure without downgrading the embedding model.

A related constraint: query and passage vectors must originate from the same model, so there is no runtime fallback to an alternative embedder. Changing models requires full re-indexing, and the embedding component is isolated behind an interface so that this remains a single substitution.

**D-30 — Latency budget.** Deterministic checks gate every model call; the two post-generation verification stages share one call while remaining separately reported; the fast model handles every task except final generation; pipeline-stage progress is streamed to the interface.

*Reasoning:* implemented naively the query pipeline is eight sequential model calls, which at typical latencies leaves the user waiting an unacceptable period before any output. These four measures reduce the common path substantially while preserving every requirement's independent verifiability.

Vision extraction is an ingestion-time cost and does not affect query latency — with one exception. Session uploads perform ingestion synchronously in response to a user action, so they default to text and OCR extraction only, with vision extraction available as an explicit opt-in.

---

## 7.6 Observability

**D-31 — Pipeline progress is streamed as Server-Sent Events over a POST request.**

*Reasoning:* nothing flows from client to server during query processing, so a bidirectional transport such as WebSockets would add connection lifecycle management for no benefit. Polling would be both laggy and wasteful. The browser's native event-source API issues only GET requests, whereas the query carries a JSON body — so the event format is retained while the response is read through a streaming fetch, avoiding the job registry and connection race that a separate GET stream would require.

The pipeline remains unaware of the transport: stages receive an emitter interface, and a no-op implementation is substituted for non-streaming calls. The non-streaming endpoint is retained alongside the streaming one, since the evaluation harness and test suite want a single response rather than a stream. A context manager wrapping each stage keeps start and completion events symmetric even when a stage raises, and captures per-stage timing as a side effect — data subsequently reported in the evaluation.

Two failure modes are guarded explicitly: intermediate proxies buffering the entire response, which makes streaming appear not to work at all; and a disconnected client leaving the pipeline running and consuming API quota.

**D-32 — Token usage is recorded within the provider-client wrapper rather than at call sites.**

*Reasoning:* a single wrapper handles credential rotation, usage recording, and usage-event emission together. Recording usage at each call site would guarantee that some calls are eventually missed, producing totals that are wrong without being visibly wrong. Binding all three concerns to one component means a call that bypasses it bypasses all three — a condition that is straightforward to detect during review.

Usage figures are read from the provider's reported metadata rather than estimated. Image inputs consume prompt tokens at a fixed rate per tile, which is why vision extraction dominates ingestion cost. Locally-executed components record zero; the web search service is counted by request rather than by token.

Token counts are the reported measure and monetary cost is derived from rates held in configuration, since published rates change. For a project operating within free tiers, proportion of quota consumed is frequently the more informative figure.

This infrastructure is required independently of the interface feature it supports: the evaluation reports per-stage cost as part of its efficiency analysis, and free-tier management depends on it operationally.

---

## 7.7 Decisions Superseded During Design

Recorded for completeness, as each represents a design position that was revised.

| Original | Revised to | Reason |
|---|---|---|
| Local GPU-based inference throughout | API-based inference (D-01) | No suitable hardware available. This reversal shaped the entire stack |
| Command-line interface only | Full web application (FRONTEND-01 to FRONTEND-05) | A demonstrable interface materially improves the deliverable |
| Deployment as a mandatory requirement | Optional extension (DEPLOY-01/02) | Scope growth elsewhere made an eight-week deployment commitment unrealistic; the core deliverable runs locally |
| Hosted reranking API | Local cross-encoder (D-25) | Better latency, no rate limit, comparable quality at this scale |
| Tesseract as primary OCR | PaddleOCR with PP-Structure (D-27) | A structure-aware baseline makes RQ2's comparison methodologically sound |
| Fixed-size chunking | Structure-aware chunking (D-12) | Fixed offsets divide sentences and tables, degrading retrieval and quoted context |
