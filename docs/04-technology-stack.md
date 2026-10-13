# 4. Technology Stack

Every component listed below is accompanied by the reason it was selected over the available alternatives. Two constraints shape nearly every choice: no dedicated GPU is available, and the project must operate within free service tiers.

---

## 4.1 Models

Large-model inference is performed through hosted APIs. A few small models run locally on CPU, in each case because doing so is faster and cheaper than the equivalent API call rather than as a compromise.

| Model | Location | Role |
|---|---|---|
| Gemini `gemini-3.5-flash` | API | Final answer generation with inline citations |
| Gemini `gemini-3.5-flash-lite` | API | Vision extraction, groundedness and safety verification, query expansion, decomposition, conversational resolution, guardrails, sufficiency assessment, web query rewriting |
| Gemini `gemini-embedding-001` | API | Dense embeddings for passages and queries |
| `ms-marco-MiniLM-L-6-v2` | Local, CPU | Cross-encoder reranking |
| `all-MiniLM-L6-v2` | Local, CPU | Relevance filter for web search results |
| Tesseract | Local, CPU | OCR for scanned pages and as the fallback for the vision path |

### Selection rationale

**Single provider for language and embedding models.** Generation, vision, and embeddings are all served by Gemini. The determining factor was operational simplicity rather than capability, since several providers offer comparable models on comparable free tiers. One provider means one credential pool to rotate and one rate limit to reason about, rather than three interacting limits whose combined behaviour under load is difficult to predict.

**Fast model for auxiliary tasks.** Only final answer generation uses the larger model. That model was planned as Gemini Pro; the available API key has no Pro access, so generation runs on `gemini-3.5-flash` (`GENERATION_MODEL`), one tier above the auxiliary model. Switching to Pro later is a configuration change. Query expansion, decomposition, guardrail classification, sufficiency assessment, and conversational resolution are all comparatively simple transformations for which the faster and cheaper model is sufficient. Since the query pipeline executes several model calls in sequence, this choice has a direct and substantial effect on end-to-end latency.

**Fast model for vision extraction.** Vision extraction is the single heaviest ingestion cost against the free tier. The faster model is the default, subject to an explicit empirical check during development: both models are run against a sample of real corpus tables and compared on transcription quality before the full corpus is processed. Whichever is selected is then held fixed for the entire corpus, since RQ2 requires a consistent extraction baseline.

**Local cross-encoder for reranking.** A 22-million-parameter cross-encoder executes in tens of milliseconds on CPU. A hosted reranking API was considered and rejected: it would have added a network round trip to every query, a third credential pool, and a third rate limit, in exchange for quality that the local model broadly matches at this corpus size. Should reranking prove to be the accuracy bottleneck during evaluation, a larger cross-encoder remains available as a drop-in replacement: more accurate, still CPU-viable, and measurably slower.

**Tesseract as the only OCR engine.** Tesseract reads a scanned page in under a second on the project's CPU-only hardware (two pages of the scanned corpus PDF measured 1.6 seconds through the extractor), installs as a single binary, and downloads no model weights. A heavier engine with table structure recognition was tried first and dropped: it took from seconds to minutes per page on the same hardware, which made scanning the corpus's 30 scanned pages the slowest part of ingestion. The cost of the choice is that Tesseract returns words in reading order and does not recover table rows and columns. For ordinary ingestion that matters little, because tables and charts on digital pages go to the vision model and OCR handles only scanned pages and vision failures. For RQ2 it defines what the OCR baseline is, which is recorded in the research methodology. See D-27.

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

**LangChain** provides the outer composition and selected components rather than every stage. Each pipeline is exposed as one named LCEL chain (`ingestion_chain`, `query_chain`) that the HTTP handlers invoke, so the handlers only validate input, pick a chain, and serialise its result. Inside a chain the stages are plain Python functions called in order, which keeps their logic directly testable. LangChain components are used where they fit: the Gemini embeddings adapter and the recursive text splitter that the structure-aware chunker builds on. Generation and vision call the Google GenAI SDK directly, and Chroma, `rank_bm25`, and Tavily are used through their own clients, because the wrapper that owns key rotation and usage recording needs direct access to each response.

Control flow remains explicit under this design. Conditional stages, including the greeting short-circuit, the cache hit path, and the web-search fallback, are ordinary branches in the pipeline function, so the code states the branching conditions rather than concealing them in a graph definition. LangGraph was considered and rejected: the query pipeline is an acyclic branching graph with no loops or replanning, so a second framework and a state-machine formulation would add complexity without capability.

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
| pytesseract | Python binding for the Tesseract OCR engine |
| Pillow | Image handling for OCR and vision extraction |

**PyMuPDF** handles both text extraction and page-to-image rendering. The rendering capability is required for the scanned-document path: a PDF page carrying no text layer must be rasterised before OCR can process it. Alternative PDF libraries offer extraction but not rendering, which would have meant maintaining two libraries for one file type.

**Installation note.** The PyTorch dependency underlying sentence-transformers defaults to a GPU build that is substantially larger than required, so the CPU-only build must be specified explicitly. The cross-encoder downloads its weights on first use, so it is initialised during environment setup rather than on first request. Tesseract is a standalone binary, vendored at `vendor/tesseract/`, and needs no model download.

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
| Structure-aware OCR engines | Recover table rows and columns, but took from seconds to minutes per page on CPU against under one second for Tesseract (D-27) |
| Dedicated guardrails frameworks | Rigorous, but guardrails are two requirements here rather than a research focus. Cited in related work as the established alternative |
| Authentication and multi-user support | Future scope; the current deliverable is a single-user academic demonstration |
| Containerisation and orchestration | Future scope, alongside public deployment; the system currently runs locally |
| Task queues | Ingestion is a one-time batch operation rather than a continuous background workload |
