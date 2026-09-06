# 4. Technology Stack

Every component listed below is accompanied by the reason it was selected over the available alternatives. Two constraints shape nearly every choice: no dedicated GPU is available, and the project must operate within free service tiers.

---

## 4.1 Models

Large-model inference is performed through hosted APIs. Two small models run locally on CPU, in both cases because doing so is faster and cheaper than the equivalent API call rather than as a compromise.

| Model | Location | Role |
|---|---|---|
| Gemini Pro | API | Final answer generation with inline citations |
| Gemini Flash | API | Vision extraction, query expansion, decomposition, conversational resolution, guardrails, sufficiency assessment |
| Gemini `gemini-embedding-001` | API | Dense embeddings for passages and queries |
| `ms-marco-MiniLM-L-6-v2` | Local, CPU | Cross-encoder reranking |
| PaddleOCR | Local, CPU | OCR fallback for the vision path (default engine) |
| PaddleOCR with PP-Structure | Local, CPU | Table structure recognition, opt-in for RQ2 baseline runs |
| Tesseract | Local, CPU | Last-resort OCR |

### Selection rationale

**Single provider for language and embedding models.** Generation, vision, and embeddings are all served by Gemini. The determining factor was operational simplicity rather than capability, since several providers offer comparable models on comparable free tiers. One provider means one credential pool to rotate and one rate limit to reason about, rather than three interacting limits whose combined behaviour under load is difficult to predict.

**Fast model for auxiliary tasks.** Only final answer generation uses the larger model. Query expansion, decomposition, guardrail classification, sufficiency assessment, and conversational resolution are all comparatively simple transformations for which the faster and cheaper model is sufficient. Since the query pipeline executes several model calls in sequence, this choice has a direct and substantial effect on end-to-end latency.

**Fast model for vision extraction.** Vision extraction is the single heaviest ingestion cost against the free tier. The faster model is the default, subject to an explicit empirical check during development: both models are run against a sample of real corpus tables and compared on transcription quality before the full corpus is processed. Whichever is selected is then held fixed for the entire corpus, since RQ2 requires a consistent extraction baseline.

**Local cross-encoder for reranking.** A 22-million-parameter cross-encoder executes in tens of milliseconds on CPU. A hosted reranking API was considered and rejected: it would have added a network round trip to every query, a third credential pool, and a third rate limit, in exchange for quality that the local model broadly matches at this corpus size. Should reranking prove to be the accuracy bottleneck during evaluation, a larger cross-encoder remains available as a drop-in replacement: more accurate, still CPU-viable, and measurably slower.

**PaddleOCR over Tesseract as the primary OCR engine.** This choice is driven by RQ2 more than by general accuracy. PaddleOCR's PP-Structure component performs table structure recognition, recovering rows and columns rather than emitting words in reading order. With a structure-blind OCR engine as the comparison baseline, a substantial portion of what RQ2 would measure is simply structured output versus unstructured output, an unsurprising result for vision extraction that says little about the question of interest. A structure-aware OCR baseline makes the comparison genuinely informative. Tesseract is retained as a fallback so that an unavailable primary engine does not block ingestion.

The three engines are wired as a cascade selected by `OCR_ENGINE`, each falling through to the next when unavailable. PP-Structure is not the default: it measures roughly 75 seconds per page on CPU against roughly 3 for plain recognition, so it is enabled for the RQ2 baseline runs that need recovered tables and left off for ordinary ingestion. See D-27 for the measurements behind that split.

**Embedding model choice and its limitation.** Larger open-weight embedding models currently achieve better retrieval benchmark results than the hosted model used here. They were not adopted because self-hosting one requires GPU memory well beyond what is available. This is recorded as a limitation in the research methodology rather than presented as a neutral choice: retrieval results reported by this project are conditioned on one embedding model, and a stronger model would plausibly shift the baseline.

A related constraint follows from this: query vectors and passage vectors must originate from the same model, so there is no runtime fallback to an alternative embedder. Changing embedding models requires re-indexing the corpus in full, and the embedding component is therefore isolated behind an interface so that such a change remains a single substitution.

---

## 4.2 Backend

| Component | Role |
|---|---|
| FastAPI | HTTP service, asynchronous request handling, automatic API documentation |
| Uvicorn | ASGI server |
| Pydantic | Passage and citation schema, request/response validation |
| pydantic-settings | Typed configuration loaded from environment |
| LangChain | Primary framework: both pipelines are composed as LCEL chains of Runnable stages |
| sentence-transformers | Executes the local cross-encoder |
| tiktoken | Token counting for chunk-size targets |
| httpx | HTTP client for services without a first-party adapter |

**FastAPI** was chosen for native asynchronous support, which the pipeline requires: dense and sparse retrieval execute concurrently, and decomposed questions retrieve for several sub-questions in parallel. Its Pydantic integration also means the API schema and the internal data model are defined once rather than maintained separately.

**Pydantic** enforces the passage schema at runtime. Since that schema is the contract between two independently developed pipelines, validation failures surface at the boundary where they occur rather than as confusing downstream errors.

**LangChain** is the primary framework rather than a supporting library. Both the ingestion and query pipelines are expressed as LCEL chains, in which each stage is a `Runnable` composed with sequence, parallel, and branch combinators, and provider access runs through LangChain adapters for Gemini, Chroma, and Tavily. Stages with no first-party component, such as reciprocal rank fusion, cross-encoder reranking, and the semantic cache, are implemented as plain functions and wrapped as runnables, which keeps their logic directly testable while the composition remains uniform.

Control flow remains explicit under this design. Conditional stages, including the greeting short-circuit, the cache hit path, and the web-search fallback, are declared as explicit branch nodes, so the chain definition states the branching conditions rather than concealing them. LangGraph was considered and rejected: the query pipeline is an acyclic branching graph with no loops or replanning, so a second framework and a state-machine formulation would add complexity without capability.

---

## 4.3 Storage and Retrieval

| Component | Role |
|---|---|
| Chroma | Vector store, persisted to local disk |
| rank_bm25 | In-process BM25 keyword index |

**Chroma** stores every passage vector with its full metadata, filterable at query time by document scope and by extraction method. Hosted vector databases were considered and rejected: a corpus of this size does not justify external infrastructure, and a local store carries two practical advantages for this project specifically. The corpus travels with the repository, so both team members work against identical data without a synchronisation step; and RQ2 requires two parallel indexes, one vision-extracted and one OCR-only, which is straightforward with local collections and awkward against a hosted free tier's storage allowance.

**rank_bm25** provides the sparse retrieval half. A dedicated search engine such as Elasticsearch would be substantial infrastructure for a corpus this size. The index is held in memory and rebuilt from persisted passage text at startup, which is effectively instantaneous here.

One consequence of this design is also recorded in the architecture document, and is repeated here because it is easy to overlook: the BM25 index maintains its own copy of passage text and offers no delete operation, so any deletion from the vector store must be followed by an index rebuild.

---

## 4.4 Document Processing

| Component | Role |
|---|---|
| PyMuPDF | PDF text extraction and page rasterisation |
| python-docx | DOCX body text and embedded image extraction |
| PaddleOCR + PaddlePaddle | Default OCR engine; PP-Structure adds table structure recognition (`paddlex[ocr]` extra) |
| pytesseract | Last-resort OCR |
| Pillow | Image handling for OCR and vision extraction |

**PyMuPDF** handles both text extraction and page-to-image rendering. The rendering capability is required for the scanned-document path: a PDF page carrying no text layer must be rasterised before OCR can process it. Alternative PDF libraries offer extraction but not rendering, which would have meant maintaining two libraries for one file type.

**Installation note.** PaddlePaddle and the PyTorch dependency underlying sentence-transformers both default to GPU builds that are substantially larger than required. CPU-only builds must be specified explicitly. Both local models also download weights on first use, so they are initialised during environment setup rather than on first request.

---

## 4.5 External Services

| Service | Role |
|---|---|
| Tavily | Web search fallback when corpus retrieval is assessed as insufficient |

Web results carry a source URL rather than a document and page reference. This distinction is preserved through reranking, generation, and citation filtering, and is surfaced in the interface, so that a user can always tell whether an answer is grounded in the document corpus or in an external source.

---

## 4.6 Frontend

| Component | Role |
|---|---|
| Next.js (App Router) | Web application framework |
| React with TypeScript | Component model and type safety across the API boundary |
| Tailwind CSS | Styling |

TypeScript types mirror the backend's Pydantic models, particularly the citation union, so that the interface's branching on citation type is checked at compile time rather than discovered at runtime.

Live pipeline progress and per-query token usage are received over a single streaming response in Server-Sent Events format, read through a streaming fetch rather than the browser's native event-source API, because that API issues only GET requests whereas the query carries a JSON body. No additional client library is required.

The frontend makes no direct calls to any model provider. All provider credentials remain server-side; the only value exposed to the client is the backend's base URL.

---

## 4.7 Evaluation

| Component | Role |
|---|---|
| RAGAS | Faithfulness, answer relevancy, context precision, context recall |
| pandas | Results aggregation |
| matplotlib | Result figures for the paper |

RAGAS was selected as an established, recognised evaluation framework for retrieval-augmented systems, in preference to bespoke metrics. Its metrics are documented and comparable to those reported elsewhere in the literature, which matters for a project whose output includes a written paper.

RAGAS itself invokes a language model to produce its judgments, so an evaluation run consumes API quota proportional to the number of questions multiplied by the number of metrics. Since the ablation studies re-run the same question set across multiple configurations, this is the point of heaviest API consumption in the project and is the principal reason the embedding cache exists.

---

## 4.8 Development Tooling

| Tool | Role |
|---|---|
| pytest with pytest-asyncio | Test suite, with asynchronous support for pipeline stages |
| ruff | Linting and formatting |
| Python 3.11+ | Backend runtime |

---

## 4.9 Technologies Not Adopted

| Technology | Reason not adopted |
|---|---|
| GPU-based local inference of large models | No suitable hardware available; the determining constraint on the whole stack |
| Hosted reranking APIs | A local cross-encoder matches them closely at this scale while removing a network round trip and a rate limit |
| Large open-weight embedding models | Better retrieval quality, but memory requirements exceed available hardware. Recorded as a limitation and revisited in future work |
| Hosted vector databases | Unwarranted infrastructure at this corpus size; local storage also simplifies the parallel indexes RQ2 requires |
| Elasticsearch or equivalent | Same reasoning applied to sparse retrieval |
| Dedicated guardrails frameworks | Rigorous, but guardrails are two requirements here rather than a research focus. Cited in related work as the established alternative |
| Authentication and multi-user support | Future scope; the current deliverable is a single-user academic demonstration |
| Containerisation and orchestration | Future scope, alongside public deployment; the system currently runs locally |
| Task queues | Ingestion is a one-time batch operation rather than a continuous background workload |
