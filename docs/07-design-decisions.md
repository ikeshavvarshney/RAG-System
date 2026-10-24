# 7. Design Decisions

A record of the significant technical decisions taken during design, with the reasoning behind each and the alternatives considered. Decisions are grouped by area and carry stable identifiers for reference from the codebase and from other documents.

---

## 7.1 Model and Service Selection

**D-01. Google Gemini as the primary model provider.** One model, `gemini-3.5-flash-lite`, handles final answer generation as well as vision extraction, answer verification, query expansion, decomposition, conversational resolution, guardrails, and sufficiency assessment.

*Reasoning:* the deciding factor was operational rather than qualitative. Several providers offer comparable models on comparable free tiers. Serving generation, vision, and embeddings from one provider means one credential pool to rotate and one rate limit to reason about, rather than three interacting limits whose behaviour under load is difficult to predict. Generation was first planned on a larger model (Gemini Pro, which the key cannot access, then `gemini-3.5-flash`) and was moved to the fast model to cut the cost and latency of the most expensive stage; `GENERATION_MODEL` remains a separate setting so it can be moved back.

**D-02. Gemini `gemini-embedding-001` for embeddings.** Same provider and credential surface as the language models.

*Alternative considered:* larger open-weight embedding models achieve better retrieval benchmark results. Self-hosting one requires GPU memory beyond what is available. Recorded as a limitation in the research methodology rather than presented as a neutral choice.

**D-03. Chroma as the vector store.** Local, disk-persisted, no external infrastructure.

*Reasoning:* a 20-50 document corpus does not justify a hosted vector database. Two practical advantages follow for this project specifically: the corpus travels with the repository so both members work against identical data, and RQ2's requirement for two parallel indexes is straightforward with local collections but awkward against a hosted free tier's storage allowance.

**D-04. `rank_bm25` for sparse retrieval.** In-process, pure Python.

*Reasoning:* a dedicated search engine would be disproportionate infrastructure at this corpus size. The index is held in memory and rebuilt from persisted passage text at startup, which is effectively instantaneous here.

**D-06. Tavily for web search fallback.** Straightforward API, free tier, results structured for programmatic use.

**D-08. Multi-key rotation across API providers.** A utility distributing requests round-robin across a pool of credentials per provider.

*Reasoning:* free-tier rate limits are the binding operational constraint on the project, particularly during the evaluation week when the question set is executed repeatedly. Built in Week 1 because retrofitting it after the pipeline exists would require modifying every call site.

---

## 7.2 Data Model

**D-09, D-10. Passage schema.** Core fields covering identity, content, and provenance, plus two fields serving purposes beyond ordinary operation.

`extraction_method` records whether a passage was produced by vision extraction, OCR, or direct text extraction. RQ2 compares the first two, and this field is what makes the comparison possible. It must be recorded accurately from the first ingestion run, including on fallback paths. A passage produced by OCR fallback but labelled as vision-extracted would silently corrupt the headline result of the study.

`corpus_scope` separates the persistent corpus from ad-hoc session uploads. Each session is held in its own Chroma directory with its own keyword index, so a session is discarded by removing a directory and cannot contaminate the corpus the ablations are measured against.

**D-11. Citations modelled as a discriminated union.** Corpus-sourced citations carry document and page; web-sourced citations carry a URL and title.

*Reasoning:* the two have genuinely different identity, and both the groundedness filter and the interface branch on the distinction. Representing it structurally rather than as nullable fields on a single type makes the branch explicit and checkable.

**D-12. Structure-aware chunking targeting 500 tokens, bounded at 200 and 800.** Segmentation at headings, paragraph breaks, and table blocks in preference to fixed offsets.

*Reasoning:* fixed-size splitting routinely divides a passage mid-sentence or mid-table, degrading both retrieval and the readability of quoted context. Tables are never split; an oversized intact table is more useful than two fragments. Token counts are measured with a tokeniser rather than approximated from character counts, since the two diverge substantially on dense text.

---

## 7.3 Structure and Process

**D-13, D-14. Monorepo with pipeline modules inside the backend package.** Ingestion and query modules are subpackages of the backend rather than top-level projects; ownership follows the module boundary.

*Reasoning:* simplest arrangement for two people over eight weeks with no deployment split. The module boundary and the ownership boundary coincide, so the two members rarely edit the same files.

**D-15, D-16. Corpus composition.** Mixed general domain, with a minimum of eight documents containing genuine tables, charts, or figures, enforced by an automated validator.

*Reasoning:* the domain choice trades question depth for collection speed and licence clarity, accepted knowingly. The chart-density floor is a methodological requirement rather than a preference: RQ2 measures a difference on visual content, and a corpus containing little visual content cannot produce a measurable difference regardless of how the extraction methods perform. Enforced automatically because a shortfall discovered during the evaluation week cannot be remedied then.

---

## 7.4 Ingestion Behaviour

**D-17. Scanned PDFs are detected and processed by OCR immediately.** A page with fewer than 50 extractable characters and a raster image covering at least half of it is rasterised and passed straight to OCR, decided per page rather than per document. Such a page is never sent to the vision model and never counts against the vision budget.

*Reasoning:* documents mixing digital and scanned pages are common, and a per-document decision handles them incorrectly in both directions.

**D-18. DOCX ingestion extracts both body text and embedded images.** The body becomes sectioned text with tables as markdown. Embedded images above a minimum size go through the same path as standalone images: the vision model first, OCR on failure.

*Reasoning:* a substantial amount of tabular content in DOCX files appears as pasted images rather than as native tables. Note also that table-cell text is not present in the document's paragraph collection and must be walked separately, or it is silently lost.

**D-47. Vision pages are chosen for the whole batch before any model call.** Every PDF page, embedded DOCX image, and standalone image is scored locally. The best candidates are kept within `MAX_VISION_PAGES` (80), at most 6 per document. The corpus ingestion script treats a plan over the cap as an error; the upload endpoint truncates and reports what it dropped.

*Reasoning:* the earlier design sent scanned pages to vision and discovered the budget halfway through a run, with the overflow falling to OCR. Planning first makes the spend known before it is incurred, spends it on pages with tables and charts rather than on plain transcription, and keeps one long report from consuming the budget of forty-nine others. A page that is not selected loses nothing it had: its text layer is still indexed.

**D-48. Passage identifiers are deterministic, and re-ingesting a document replaces it.** An identifier is a hash of the passage's scope, document, extraction method, chunk type, page, location, position, and text. Before a re-ingested document is indexed, its previous passages are deleted.

*Reasoning:* random identifiers made every re-ingestion add a second copy of each passage, which inflates retrieval results and distorts the ablations. Deterministic identifiers make an unchanged passage a no-op, and the delete-first step removes passages that an edited file no longer produces.

**D-49. OCR results are cached by image.** The key covers the pixels and the engine name. A failed recognition is never stored.

*Reasoning:* the cache was introduced when OCR measured minutes per page on CPU and a stopped run lost all of it. With Tesseract a page costs about a second, so the cache now matters less, but it still keeps a repeated ingestion run from recognising the same page twice. Including the engine name in the key means entries written by a different OCR engine are never served as Tesseract output, which would otherwise silently corrupt RQ2's OCR baseline.

**D-19. Per-file error isolation.** An unsupported, corrupt, or unreadable file is skipped with a recorded error; the remainder of the batch proceeds.

*Reasoning:* ingesting a corpus of fifty documents must not abort on the twelfth. Failures are reported per file with their cause, and each distinct cause reads differently: an oversized file is reported as too large rather than being passed on as empty bytes and surfacing as a corrupt document.

**D-26. PyMuPDF for PDF handling.** Text extraction and page rasterisation from one library.

*Reasoning:* the scanned-document path requires rendering pages to images. Alternative PDF libraries provide extraction but not rendering, which would mean maintaining two libraries for one file type.

**D-27. Tesseract as the only OCR engine.**

*Original decision.* A heavier OCR engine with table structure recognition was the primary engine and the RQ2 baseline, with Tesseract as a last-resort fallback. Structure recognition recovers rows and columns rather than emitting words in reading order, which would have made RQ2 a test of whether vision-language understanding adds value beyond accurate structural transcription.

*Revised after measurement.* That engine was removed and Tesseract is now the only one. On this project's CPU-only hardware structure recognition cost roughly 75 seconds per page, and the cost proved to be model-bound rather than resolution-bound: rendering pages at 100, 150, and 200 DPI produced 96, 75, and 81 seconds respectively, so it could not be tuned away by sending smaller images. Its plain recognition mode took roughly 3 seconds per page on the first machine and 2.5 to 6 minutes per page on the second, slower one, where ingesting 49 documents took about 90 minutes, an hour of it OCR on 18 pages. Tesseract measured 1.6 seconds for two scanned corpus pages on that slower machine. The change also removes roughly seventy Python packages, a large CPU wheel, and close to 2 GB of downloaded model weights from the setup.

*What is given up.* Tesseract returns text in reading order and does not recover table structure. For ordinary ingestion this costs little: vision handles figures and tables, so OCR runs only on scanned pages (D-17) and on vision failures. For RQ2 the OCR baseline is now structure-blind, so part of any measured difference reflects structured versus flattened output rather than vision-language understanding alone. This is recorded as a threat to validity in the research methodology rather than hidden.

**D-28. Fast model for vision extraction, subject to empirical verification.** Both available models are compared against a sample of real corpus tables before the full corpus is processed; the selection is then held fixed.

*Reasoning:* vision extraction is the heaviest ingestion cost against the free tier, and the faster model is substantially cheaper. Whether it is sufficient is a question about actual output quality on actual documents, so it is settled by measurement rather than assumption. Holding the selection fixed thereafter is required for RQ2's baseline consistency.

---

## 7.5 Query Behaviour

**D-21. The answer cache is keyed by document scope and by resolved question, and records the documents each answer cited.**

*Reasoning:* three consequences follow, each addressing a distinct correctness failure. Session isolation prevents an answer derived from one user's uploads being served to another. Recording cited documents allows cache invalidation when a document is deleted, without which the system would continue serving an answer citing a document that no longer exists. Keying on the resolved rather than the literal question prevents a follow-up such as "what about 2024?" from colliding across unrelated conversations.

Because a cache hit bypasses the entire pipeline, no downstream stage can catch an incorrect hit. The similarity threshold is therefore set conservatively. Only verified, cited answers drawn from the corpus alone are written: an answer citing web sources would go stale with nothing to invalidate it, and an answer from a session's own uploads is not cached at all.

**D-22. The keyword index is rebuilt after any corpus change.**

*Reasoning:* the BM25 implementation maintains its own in-memory copy of passage text and provides no delete operation. A document removed from the vector store remains fully searchable through the sparse path, and can still be retrieved and quoted into an answer, until the index is rebuilt. At this corpus size a complete rebuild is effectively instantaneous, so no incremental approach is warranted.

**D-23. Document deletion reaches three locations.** Vector store, keyword index, and answer cache.

*Reasoning:* omitting any one leaves deleted content reachable by some path. The extraction, OCR, and embedding caches are keyed by content hash, not by document, and are not a retrieval path, so they are deliberately excluded. Deletion is scoped, so that a session may delete only its own uploads, and operates on documents rather than passages, matching the user's mental model.

**D-24. Conversational context is bounded to the last 2-3 turns, and follow-up questions are resolved into self-contained form before any downstream stage.**

*Reasoning:* retrieval, expansion, decomposition, and caching are all incapable of handling an unresolved pronoun, so resolution must precede them. The window is bounded deliberately: full conversation history costs tokens on every call and actively degrades resolution quality once the topic changes. Questions that are already self-contained pass through unmodified, since an over-eager rewriter that attaches prior context to an unrelated new question is worse than no history at all.

**D-25. Reranking uses a local cross-encoder rather than a hosted reranking API.**

*Reasoning:* a 22-million-parameter cross-encoder executes in tens of milliseconds on CPU. A hosted alternative would add a network round trip to every query, a third credential pool, and a third rate limit, in exchange for quality the local model broadly matches at this corpus size. A larger cross-encoder remains available as a drop-in replacement should reranking prove to be the accuracy bottleneck during evaluation.

**D-50. Sufficiency is gated on dense similarity, with a model verdict in the uncertain band.** At a best cosine similarity of 0.90 or above the passages pass, below 0.78 they fail, and in between a model reads the top five passages in full and decides. Session uploads are never failed on the score alone.

*Reasoning:* fused scores carry no absolute relevance and BM25 scores are unbounded, so cosine similarity is the only usable cheap signal. The thresholds were calibrated on 24 corpus questions. The lower one was first 0.82 and was moved to 0.78 in Week 6 after answerable questions scored 0.819 (a DOCX table) and 0.801 (a broad question about one uploaded document) and were sent to web search without the passages being read; questions the corpus clearly cannot answer score 0.75 to 0.76. The verdict originally saw only the first 700 characters of each passage and missed answers that sat past that point, so it now reads up to 4000. A false "insufficient" is not harmless: the web results it brings in can be about unrelated meanings of the question, which is why generation is also told not to add them when the documents already answer (QUERY-09).

**D-51. Cost estimates count everything that is billed.** Output tokens include the model's thinking tokens, and each web search is reported with its search depth.

*Reasoning:* Gemini bills thinking tokens at the output rate but reports them apart from the answer tokens, so counting only the latter understated the most expensive stage. Tavily bills per request by depth (one credit for basic, two for advanced), not by tokens, so a search is priced from its depth label rather than from a token count. The prices themselves live in the frontend, taken from the providers' published pricing pages.

**D-29. Embeddings are cached by content hash.**

*Reasoning:* the ablation studies re-run the same question set across multiple configurations, producing thousands of redundant embedding requests for identical text. The cache addresses the resulting quota pressure without downgrading the embedding model.

A related constraint: query and passage vectors must originate from the same model, so there is no runtime fallback to an alternative embedder. Changing models requires full re-indexing, and the embedding component is isolated behind an interface so that this remains a single substitution.

**D-30. Latency budget.** Deterministic checks gate every model call; the two post-generation verification stages share one call while remaining separately reported; the fast model handles every task except final generation; pipeline-stage progress is streamed to the interface.

*Reasoning:* implemented naively the query pipeline is eight sequential model calls, which at typical latencies leaves the user waiting an unacceptable period before any output. These four measures reduce the common path substantially while preserving every requirement's independent verifiability.

Vision extraction is an ingestion-time cost and does not affect query latency, with one exception. Session uploads perform ingestion synchronously in response to a user action. They go through the same vision selection as the corpus (D-47), with one difference: an upload whose plan exceeds the vision cap keeps its best pages and reports the rest as dropped instead of failing.

---

## 7.6 Observability

**D-31. Pipeline progress is streamed as Server-Sent Events over a POST request.**

*Reasoning:* nothing flows from client to server during query processing, so a bidirectional transport such as WebSockets would add connection lifecycle management for no benefit. Polling would be both laggy and wasteful. The browser's native event-source API issues only GET requests, whereas the query carries a JSON body. The event format is therefore retained while the response is read through a streaming fetch, avoiding the job registry and connection race that a separate GET stream would require.

The pipeline remains unaware of the transport: stages receive an emitter interface, and a no-op implementation is substituted for non-streaming calls. The non-streaming endpoint is retained alongside the streaming one, since the evaluation harness and test suite want a single response rather than a stream. A context manager wrapping each stage keeps start and completion events symmetric even when a stage raises, and captures per-stage timing as a side effect. That timing data is subsequently reported in the evaluation.

Two failure modes are guarded explicitly: intermediate proxies buffering the entire response, which makes streaming appear not to work at all; and a disconnected client leaving the pipeline running and consuming API quota.

**D-32. Token usage is recorded within the provider-client wrapper rather than at call sites.**

*Reasoning:* a single wrapper handles credential rotation, usage recording, and usage-event emission together. Recording usage at each call site would guarantee that some calls are eventually missed, producing totals that are wrong without being visibly wrong. Binding all three concerns to one component means a call that bypasses it bypasses all three, a condition that is straightforward to detect during review.

Usage figures are read from the provider's reported metadata rather than estimated. Image inputs consume prompt tokens at a fixed rate per tile, which is why vision extraction dominates ingestion cost. Locally-executed components record zero; the web search service is counted by request rather than by token.

Token counts are the reported measure and monetary cost is derived from rates held in configuration, since published rates change. For a project operating within free tiers, proportion of quota consumed is frequently the more informative figure.

This infrastructure is required independently of the interface feature it supports: the evaluation reports per-stage cost as part of its efficiency analysis, and free-tier management depends on it operationally.

**D-52. Every HTTP request and every answered query is logged with a shared request identifier.** The backend logs method, path, status, and duration once the response body has been sent, plus one summary line per answered query. The identifier is returned in the `X-Request-ID` header, and the frontend prints the same identifier in its own request log.

*Reasoning:* the application's informational logs were previously invisible under the server's default configuration, which made a failed query hard to trace. Timing a request to the end of its body, rather than to its headers, is what makes the duration of a streamed query meaningful. A shared identifier lets a line in the browser console be matched to its line in the server log.

---

## 7.7 Decisions Revised During Design

Recorded for completeness, as each represents a design position that was reconsidered and changed.

| Original position | Revised to | Reason |
|---|---|---|
| Local GPU-based inference throughout | API-based inference (D-01) | No suitable hardware available. This reversal shaped the entire stack |
| Command-line interface only | Full web application (FRONTEND-01 to FRONTEND-05) | A demonstrable interface materially improves the deliverable |
| Deployment as a mandatory requirement | Future scope (DEPLOY-01, DEPLOY-02) | Scope growth elsewhere made an eight-week deployment commitment unrealistic; the core deliverable runs locally |
| Hosted reranking API | Local cross-encoder (D-25) | Better latency, no rate limit, comparable quality at this scale |
| A structure-aware OCR engine as primary and as the RQ2 baseline, with Tesseract as fallback | Tesseract as the only OCR engine (D-27) | The structure-aware engine took from seconds to minutes per page on CPU; Tesseract takes under one second. RQ2's OCR baseline is now structure-blind, recorded as a limitation |
| Scanned pages sent to vision, overflow past the page budget sent to OCR | Scanned pages straight to OCR (D-17); vision pages planned per batch (D-47) | Spends the vision budget on tables and charts, and makes the spend known before the run |
| Sufficiency failed on score alone below 0.82 | Model verdict from 0.78, and always for session uploads (D-50) | Answerable questions were being sent to web search unread |
| Fixed-size chunking | Structure-aware chunking (D-12) | Fixed offsets divide sentences and tables, degrading retrieval and quoted context |
