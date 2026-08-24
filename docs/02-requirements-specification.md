# 2. Requirements Specification

Each requirement carries a stable identifier used throughout the project documentation, the codebase, and the traceability matrix in §2.8.

---

## 2.1 Document Ingestion

| ID | Requirement |
|---|---|
| **INGEST-01** | The system routes uploaded PDF, DOCX, and image files by type to the appropriate extraction path. |
| **INGEST-02** | The system extracts charts and tables using a vision-language model, falling back to OCR when vision extraction fails or is unavailable. |
| **INGEST-03** | The system segments extracted text using a structure-aware strategy that respects document boundaries such as headings, paragraphs, and table blocks, rather than fixed-size splitting. |
| **INGEST-04** | The system indexes every passage twice — as a dense embedding vector in the vector store, and as raw text in the keyword index. |

## 2.2 User-Uploaded Documents

| ID | Requirement |
|---|---|
| **USERDOC-01** | A user may upload up to 5 documents at query time and have retrieval scoped to those documents alone, separate from the persistent corpus. |
| **USERDOC-02** | A user may delete a previously uploaded document. Its passages are removed from both indexes and cease to appear in retrieval results and citations. |

## 2.3 Query Processing

| ID | Requirement |
|---|---|
| **QUERY-01** | The system identifies greetings and small talk and responds directly without invoking retrieval. |
| **QUERY-02** | The system checks a semantic answer cache and returns a cached answer on a match, bypassing the remainder of the pipeline. |
| **QUERY-03** | On a cache miss, the system expands the query into 3-5 model-generated reformulations. The count is configurable. |
| **QUERY-04** | The system executes dense vector search and sparse keyword search in parallel across the original and expanded queries. |
| **QUERY-05** | The system merges dense and sparse result sets into a single ranking using Reciprocal Rank Fusion. |
| **QUERY-06** | The system assesses whether the fused results are sufficient to answer the question. |
| **QUERY-07** | When corpus retrieval is assessed as insufficient, the system falls back to web search for supplementary context. |
| **QUERY-08** | The system reranks fused context using a cross-encoder and consolidates it before generation. |
| **QUERY-09** | The system generates an answer from the reranked context with mandatory inline citations. |
| **QUERY-10** | The system filters claims not supported by the cited context before returning the answer. |
| **QUERY-11** | The system writes the generated answer to the semantic cache for subsequent retrieval. |
| **QUERY-12** | The system decomposes complex multi-part questions into sub-questions, retrieves for each independently, and synthesises a combined answer. |
| **QUERY-13** | The system validates and sanitises incoming queries before processing them. |
| **QUERY-14** | The system checks generated answers against safety and quality criteria before returning them. This is a distinct check from the groundedness filtering in QUERY-10. |
| **QUERY-15** | The system retains the last 2-3 conversational turns and resolves dependent follow-up questions against that history before retrieval. |

## 2.4 Backend Service

| ID | Requirement |
|---|---|
| **BACKEND-01** | A FastAPI service exposes ingestion and query endpoints, with LangChain orchestrating the internal pipeline stages. |

## 2.5 Web Interface

| ID | Requirement |
|---|---|
| **FRONTEND-01** | A Next.js and Tailwind CSS web application allows a user to upload documents and submit questions. |
| **FRONTEND-02** | The application displays generated answers together with their citations, distinguishing corpus-sourced citations (document and page) from web-sourced citations (URL). |
| **FRONTEND-03** | The application displays the live status of backend pipeline stages while a query is being processed. |
| **FRONTEND-04** | The application displays token usage and estimated cost for each query, broken down by pipeline stage. |
| **FRONTEND-05** | The application displays a fact-check score for web-sourced answers, derived from the per-claim groundedness verdicts produced by QUERY-10. |

## 2.6 Evaluation and Research

| ID | Requirement |
|---|---|
| **EVAL-01** | A hand-constructed question-answer set drawn from the corpus is scored using the RAGAS metrics: faithfulness, answer relevancy, context precision, and context recall. |
| **RESEARCH-01** | Fusion weighting is ablated. Reciprocal Rank Fusion is evaluated across a range of dense/sparse balance settings, with metric deltas reported per setting. |
| **RESEARCH-02** | Multimodal extraction is ablated. Vision-model extraction is compared against OCR-only extraction on table- and chart-dependent questions, with metric deltas reported. |
| **PAPER-01** | A research paper is produced covering methodology, system architecture, related work, and the results of both ablation studies. |

## 2.7 Optional Requirements

Not required for project completion. Attempted only if the core deliverables are complete with time remaining.

| ID | Requirement |
|---|---|
| **DEPLOY-01** | The frontend is deployed and reachable over the internet. |
| **DEPLOY-02** | The backend is hosted such that the deployed frontend can reach it end to end. |

---

## 2.8 Traceability

31 mandatory requirements, distributed across the eight-week schedule. The frontend requirements are developed on a parallel track spanning weeks 4-6, since they depend on backend endpoints becoming available progressively rather than all at once.

| Week | Stage | Requirements |
|---|---|---|
| 1 | Design lock and environment setup | — (enables all subsequent work) |
| 2 | Ingestion, part 1 | INGEST-01 |
| 3 | Ingestion, part 2 | INGEST-02, INGEST-03, INGEST-04 |
| 4 | Query pipeline, part 1 | QUERY-01, QUERY-02, QUERY-03, QUERY-04, QUERY-13, QUERY-15, USERDOC-01, USERDOC-02 |
| 5 | Query pipeline, part 2 | QUERY-05, QUERY-06, QUERY-07, QUERY-08, QUERY-12 |
| 6 | Generation and service layer | QUERY-09, QUERY-10, QUERY-11, QUERY-14, BACKEND-01 |
| 7 | Evaluation and ablations | EVAL-01, RESEARCH-01, RESEARCH-02 |
| 8 | Paper and demonstration | PAPER-01 |
| 4-6 (parallel) | Web interface | FRONTEND-01, FRONTEND-02, FRONTEND-03, FRONTEND-04, FRONTEND-05 |
| Optional | Deployment | DEPLOY-01, DEPLOY-02 |

**Coverage:** 31 of 31 mandatory requirements are assigned to a specific stage. No requirement is unassigned.

---

## 2.9 Requirement Boundaries Worth Noting

Three pairs of requirements are deliberately kept distinct, as the difference is easy to collapse and each is separately assessable.

**QUERY-10 and QUERY-14** address different failure modes. QUERY-10 asks whether the answer is *supported by the retrieved context* — a factual grounding question. QUERY-14 asks whether the answer is *safe and of acceptable quality* — independent of whether it is well-grounded. An answer can be perfectly grounded and still fail QUERY-14, or be entirely benign while failing QUERY-10.

**QUERY-03 and QUERY-12** both produce multiple queries from one input, but for different reasons. Expansion (QUERY-03) generates paraphrases of a single question to improve recall; every result addresses the same information need. Decomposition (QUERY-12) splits a compound question into genuinely distinct sub-questions, each requiring its own retrieval and contributing a different part of the final answer.

**USERDOC-01 and QUERY-15** both make use of a session identifier but serve unrelated purposes — the former scopes retrieval to a set of uploaded documents, the latter maintains conversational context. They share an identifier as an implementation convenience, not because they are the same concern.
