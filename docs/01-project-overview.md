# 1. Project Overview

## 1.1 Problem Statement

Retrieval-Augmented Generation grounds a language model's output in retrieved source material, reducing fabrication and allowing answers to cite where their information came from. Standard RAG implementations, however, carry two structural weaknesses that become apparent as soon as they are applied to real-world documents.

**Loss of non-textual information.** Reports, research papers, statistical bulletins, and technical documentation express a substantial portion of their content through tables, charts, and figures. Standard text extraction either discards this content entirely or reduces it to a positionally-ordered sequence of characters in which the relationship between a value and its row and column header is destroyed. A question whose answer lies in a table is therefore frequently unanswerable, even when the document containing it has been correctly retrieved.

**Single-mode retrieval.** Dense vector retrieval matches on semantic similarity and handles paraphrase well, but degrades on queries containing exact identifiers, part numbers, statute references, or uncommon proper nouns, precisely the cases where lexical matching is most reliable. Sparse keyword retrieval has the complementary profile. Systems committing to one mode inherit that mode's blind spot.

A third, secondary issue motivates part of the design: users interact with a question-answering system conversationally. A question such as "why did it decline?" is meaningless to a retriever in isolation, yet is entirely natural as the second turn of a conversation.

## 1.2 Objectives

### Primary Objective

Build a working end-to-end retrieval-augmented generation system that:

- ingests a corpus of 20-50 documents spanning PDF, DOCX, and image formats;
- extracts textual, tabular, and graphical content through a vision-language model with an OCR fallback path;
- indexes each resulting passage for both dense and sparse retrieval;
- answers natural-language questions with inline citations traceable to a specific document and page;
- exposes this functionality through a web interface.

### Research Objective

Treat two design choices as controlled experiments and report quantitative results:

**RQ1. Fusion weighting.** In hybrid retrieval, how does varying the relative weight of dense and sparse retrieval in Reciprocal Rank Fusion affect answer quality? Specifically, does hybrid retrieval measurably outperform dense-only retrieval on this corpus, and at what balance?

**RQ2. Multimodal extraction.** Does vision-language-model extraction of tables and charts produce measurably better answers than OCR-only extraction, on questions whose answers reside in that visual content?

Both questions are evaluated using the RAGAS metric suite against a hand-constructed reference question-answer set.

### Deliverables

1. The implemented system, running locally.
2. A research paper covering methodology, architecture, related work, and the results of both ablation studies.
3. A reproducible evaluation harness and the reference QA set.
4. A live demonstration.

## 1.3 Scope

### Current Scope

| Area | Included |
|---|---|
| **Document ingestion** | Type-based routing for PDF/DOCX/image; text-layer extraction; vision-model extraction of tables and charts; OCR for scanned documents, embedded images, and vision fallback; structure-aware chunking; dual indexing |
| **Retrieval** | Multi-query expansion; parallel dense and sparse search; Reciprocal Rank Fusion; sufficiency assessment; web-search fallback; cross-encoder reranking |
| **Generation** | Cited answer generation; groundedness filtering; output safety checks; semantic answer caching |
| **Conversation** | Greeting short-circuit; follow-up question resolution against recent turns |
| **User documents** | Ad-hoc upload of up to 5 documents per session with scoped retrieval; document deletion |
| **Interface** | Web application supporting upload, chat-style querying, citation display, live pipeline-stage feedback, and per-query token/cost reporting |
| **Evaluation** | RAGAS scoring over a hand-built QA set; two ablation studies |

### Future Scope

The following are deliberately deferred beyond the eight-week delivery. Each is a natural extension of the current system rather than a discarded idea, and the rationale records the constraint that defers it.

| Deferred to future work | Rationale |
|---|---|
| Multi-user accounts and authentication | The current deliverable is an academic demonstration; a deployed multi-user service is a follow-on stage |
| Production operations: rate limiting, monitoring, SLAs | Follows from the same transition to a deployed service |
| Distributed or horizontally-scaled infrastructure | A 20-50 document corpus does not require it; warranted once corpus size grows by an order of magnitude |
| Local GPU-based model inference | Constrained by currently available hardware; all large-model inference is API-based for now |
| Fine-tuning or training of any model | The present contribution is system design and evaluation rather than model development; domain-adapted embedding or generation models are a subsequent research direction |
| Public deployment: frontend to a hosting platform, backend to a reachable host | Treated as a stretch goal (DEPLOY-01, DEPLOY-02). Not required for project completion; attempted only if the core deliverables are finished with time remaining |

## 1.4 Constraints

**Hardware.** No dedicated GPU is available. All large-model inference, covering generation, vision extraction, and embeddings, is performed through hosted APIs. Two small models (a cross-encoder reranker and an OCR engine) run locally on CPU, where doing so is faster and cheaper than an equivalent API call.

**Cost.** The project operates entirely within free service tiers. This constrains request volume and shapes several design decisions, most visibly the multi-key rotation utility and the caching layers described in [04. Technology Stack](04-technology-stack.md).

**Timeline.** Eight weeks, with one major build stage per week. The schedule and its risk points are set out in [06. Implementation Plan](06-implementation-plan.md).

**Corpus.** 20-50 persistently indexed documents. A meaningful proportion must contain genuine tables and charts, as RQ2 cannot produce a measurable result otherwise. Ad-hoc user uploads are capped at 5 documents per session.

**Team.** Two members, with the ingestion and query pipelines assigned to separate owners. This makes the passage schema a formal interface contract between the two halves of the system rather than an internal detail.

## 1.5 Expected Outcomes

On completion the project will have produced:

- A functioning multimodal RAG system, demonstrable end to end against a real document corpus.
- A quantitative answer to RQ1, reporting how retrieval quality varies across the dense/sparse fusion spectrum on this corpus.
- A quantitative answer to RQ2, reporting the measured difference between vision-based and OCR-only extraction on table- and chart-dependent questions.
- A written paper presenting the architecture, methodology, and both results.

Both research questions are posed as genuine questions. A result showing that hybrid retrieval does not outperform dense-only retrieval on this corpus, or that OCR-only extraction is competitive with vision extraction, would be a valid and reportable finding. The experimental design is fixed in advance in [05. Research Methodology](05-research-methodology.md) so that the outcome is determined by measurement rather than by expectation.
