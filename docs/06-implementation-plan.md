# 6. Implementation Plan

Eight weeks, one major build stage per week. Each stage produces something independently verifiable, so that progress is measurable rather than assumed.

---

## 6.1 Work Distribution

| Member | Primary responsibility |
|---|---|
| **Member A** | Document ingestion pipeline; evaluation harness and reference question set; multimodal extraction ablation (RQ2) |
| **Member B** | Query pipeline; web interface; fusion weighting ablation (RQ1); research paper |

The division follows the system's natural seam. The ingestion pipeline writes passages; the query pipeline reads them. Provided both sides build against the agreed passage schema, they can be developed and tested independently for the majority of the project.

The two ablations are assigned so that each member evaluates the subsystem they built and understand best.

Integration work, the final report, and the demonstration are shared.

---

## 6.2 Schedule

### Week 1 — Design Lock and Environment Setup

**Objective:** both members can run the project locally and begin building against a shared, unambiguous interface.

| Owner | Work |
|---|---|
| A | Repository structure; dependency manifest; typed configuration; provider-client wrapper handling credential rotation and token accounting; service skeleton with health endpoint |
| B | Frontend scaffold with typed API client; corpus validator; corpus collection |
| Both | Passage and citation schema — written jointly and agreed by both |
| Both | API credentials; local model weights initialised during setup |

**Verification:** backend test suite passes; frontend builds without error; corpus validator passes against the collected corpus; both members can run the full stack locally.

The passage schema is written jointly rather than by one member because it is the contract both pipelines depend on for the following six weeks. Time spent agreeing it now is considerably cheaper than a mid-project migration affecting both halves of the system.

---

### Week 2 — Ingestion Pipeline, Part 1

**Objective:** a file uploaded in any supported format routes to the correct extraction path and produces passages end to end.

**Requirements:** INGEST-01

| Owner | Work |
|---|---|
| A | Type-based routing with content verification; PDF text-layer extraction; scanned-page detection and OCR; DOCX body text and embedded image extraction; image loading and OCR; provisional fixed-size chunking; per-file error handling |
| B | Frontend chat shell against mocked responses; review of passage schema usage |

**Verification:** the pipeline completes on at least one document of each supported type from the real corpus; scanned PDFs and DOCX-embedded images produce OCR-derived passages; a corrupt file and an unsupported type are both skipped with recorded errors while the batch completes.

Chunking at this stage is a deliberate placeholder. Structure-aware chunking arrives in Week 3; the intent here is to establish a working path end to end before adding the more complex extraction and segmentation logic.

---

### Week 3 — Ingestion Pipeline, Part 2

**Objective:** ingestion is complete; the corpus is fully indexed and searchable through both retrieval channels.

**Requirements:** INGEST-02, INGEST-03, INGEST-04

| Owner | Work |
|---|---|
| A | Vision extraction of tables and charts, with OCR fallback; structure-aware chunking replacing the placeholder; embedding with content-hash caching; vector store and BM25 index construction; full-corpus ingestion run |
| B | Query pipeline skeleton in preparation for Week 4 |

**Verification:** documents containing tables produce structured passages tagged as vision-extracted; forced vision failure falls back to OCR with the cause logged; chunk boundaries respect document structure and tables remain intact; passage counts match between the two indexes; re-ingestion produces no duplicates.

The full-corpus run at the end of this week is the first substantial workload against the API free tier and the first measurement of how much visual content the corpus actually yields. If vision-extracted passages prove sparse, the corpus or the extraction trigger requires attention here — the alternative is discovering it in Week 7, when there is no time to respond.

---

### Week 4 — Query Pipeline, Part 1

**Objective:** a question is validated, resolved against conversation history, cache-checked, expanded, and searched through both retrieval channels.

**Requirements:** QUERY-01, QUERY-02, QUERY-03, QUERY-04, QUERY-13, QUERY-15, USERDOC-01, USERDOC-02

| Owner | Work |
|---|---|
| B | Input guardrails; greeting short-circuit; conversational resolution; semantic cache lookup; multi-query expansion; parallel dense and sparse retrieval; session upload with scoped retrieval; document listing and deletion |
| A | Support for ingestion reuse in the upload and deletion paths; drafting of reference questions while the corpus is freshly in mind |

**Verification:** greetings short-circuit without retrieval; follow-up questions resolve correctly against prior turns; cached answers are returned without cross-conversation collision; session uploads are retrievable only within their own scope; deleted documents disappear from both retrieval channels and from the cache.

Pipeline-stage instrumentation is added during this week even though nothing consumes it yet. FRONTEND-03 requires live stage progress, and instrumenting a pipeline as it is written is substantially cheaper than retrofitting a completed one.

---

### Week 5 — Query Pipeline, Part 2

**Objective:** retrieved candidates are fused, assessed, supplemented where necessary, and reranked into clean context.

**Requirements:** QUERY-05, QUERY-06, QUERY-07, QUERY-08, QUERY-12

| Owner | Work |
|---|---|
| B | Reciprocal Rank Fusion with configurable weighting; sufficiency assessment; web search fallback; cross-encoder reranking and consolidation; query decomposition |
| A | Preparatory scripting for the RQ2 extraction comparison |

**Verification:** the complete query pipeline runs end to end against the real corpus, including a decomposed multi-part question and a question triggering web fallback.

The fusion weighting is implemented as a configuration parameter from its first commit, since RQ1 varies it. Implementing it as a constant would mean beginning Week 7 with a refactor that Week 7 has no capacity to absorb.

Output quality at this stage is expected to be rough. Refinement occurs in Week 6, and Week 7's evaluation identifies what actually requires attention rather than what appears to.

---

### Week 6 — Generation, Citations, and Service Layer

**Objective:** reranked context becomes a cited, verified answer, exposed through a stable API.

**Requirements:** QUERY-09, QUERY-10, QUERY-11, QUERY-14, BACKEND-01

| Owner | Work |
|---|---|
| B | Cited answer generation, including synthesis for decomposed questions; groundedness filtering; output guardrails; cache and history write-back; API finalisation with stage streaming |
| A | Integration testing against the real corpus; continued evaluation preparation |

**Verification:** a defined set of end-to-end scenarios — corpus question, cached repeat, web-fallback question, decomposed question, session-scoped question, greeting, rejected input, table-dependent question, follow-up question, and post-deletion question — all complete correctly against the real corpus.

This scenario set doubles as the demonstration script for Week 8, so completing all of them cleanly has value beyond verification.

---

### Week 7 — Evaluation and Ablation Studies

**Objective:** system quality is measured, and both research questions are answered quantitatively.

**Requirements:** EVAL-01, RESEARCH-01, RESEARCH-02

| Owner | Work |
|---|---|
| A | Reference question set finalised; RAGAS baseline established; RQ2 extraction ablation |
| B | RQ1 fusion weighting ablation; defect resolution arising from evaluation |
| Both | Paper drafting — methodology and results sections |

**Verification:** both ablation studies produce complete result tables with real measured deltas.

This is the week the project's research contribution is produced. Three elements prepared earlier make it feasible: the reference questions drafted during Weeks 4 and 5, the fusion weighting having been configurable since Week 5, and the cost and timing instrumentation built in Week 1, which supplies the efficiency figures reported alongside each ablation at no additional effort.

The sequencing within the week is fixed: establish baseline, resolve defects, re-establish baseline, then run both ablations against the final pipeline. Any behavioural change invalidates results collected before it, and a five-configuration sweep is too expensive to repeat.

---

### Week 8 — Paper, Demonstration, and Buffer

**Objective:** the research paper is complete and the demonstration is prepared.

**Requirements:** PAPER-01

| Owner | Work |
|---|---|
| B | Paper: introduction, related work, discussion, conclusion; integration of Week 7 material |
| A | Demonstration script and rehearsal; methodology detail for ingestion and evaluation |
| Both | Final review; documentation of setup procedure; clean-installation verification |

**Verification:** the paper is complete with every reported figure traceable to a results file; the demonstration has been rehearsed end to end with a fallback recording captured; a clean repository checkout can be brought to a running state by following the documented setup procedure alone.

The methodology and results sections are drafted in Week 7 rather than deferred here. Week 8 covers the framing sections, the demonstration, and slack — which experience suggests will be consumed by defects the demonstration rehearsal surfaces.

---

## 6.3 Parallel Track — Web Interface

**Requirements:** FRONTEND-01, FRONTEND-02, FRONTEND-03, FRONTEND-04, FRONTEND-05

The interface is not allocated a week of its own. It develops alongside Weeks 4 through 6, as backend endpoints become available:

| Period | Work |
|---|---|
| Week 4 | Chat shell, typed API client, mocked responses |
| Week 5 | Upload panel with per-file status; document deletion; live queries replacing mocks; multi-turn conversation |
| Week 6 | Citation display distinguishing corpus and web sources; live pipeline-stage progress; token usage and cost display |
| Week 8 buffer | Responsive layout, keyboard handling, accessibility, markdown rendering |

FRONTEND-03 and FRONTEND-04 both depend on the backend emitting structured events during query processing. That instrumentation is added in Week 4 and exposed as a stream in Week 6, so the dependency is satisfied by sequencing rather than left to chance. The token-usage tracking underlying FRONTEND-04 is built in Week 1 alongside credential rotation, since both are properties of the same provider-client wrapper and neither can be retrofitted without modifying every call site.

---

## 6.4 Risk Assessment

Stated openly rather than discovered late. The requirement set is substantial for two people in eight weeks, and the following are the points most likely to come under pressure.

| Rank | Risk | Assessment | Mitigation |
|---|---|---|---|
| 1 | **Week 7 overload** | Constructing the question set, establishing a baseline, resolving defects, and running two ablation sweeps within one week is the densest point in the schedule. Any slippage in Weeks 4-6 compounds here | Reference questions drafted from Week 4; fusion weighting configurable from Week 5; evaluation harness parameterised so both sweeps reuse one implementation |
| 2 | **Week 5 complexity** | Fusion, sufficiency assessment, web fallback, reranking, and decomposition constitute five distinct pieces of branching logic in one week. Decomposition in particular multiplies retrieval cost per question | Decomposition is the last item; if the week slips, it is the flagged item rather than a silently dropped one |
| 3 | **Week 6 convergence** | Generation, two verification stages, cache write-back, and API finalisation all land together, immediately before the evaluation week | The end-to-end scenario set provides a concrete completion criterion rather than a subjective one |
| 4 | **Frontend stage monitoring** | FRONTEND-03 requires backend instrumentation not present in the original design | Instrumentation added Week 4, exposed Week 6 — dependency resolved by sequencing |
| 5 | **API rate limits** | Free-tier limits bind hardest during Week 7's repeated evaluation runs | Key rotation from Week 1; embedding and extraction caches; checkpointed evaluation runs that resume rather than restart |

**If schedule pressure materialises,** the responses in order of preference are: treat deployment as fully dropped rather than attempted, which is already the plan; reduce the scope of frontend polish; and, if Weeks 5-6 slip materially, raise decomposition or the output guardrail layer for explicit discussion rather than reducing scope silently.

The evaluation week is protected in preference to feature completeness. A system with one fewer pipeline stage and two clean ablation studies is a stronger deliverable than a feature-complete system with no measured results.

---

## 6.5 Interface Stability

Two agreements govern parallel work:

**The passage schema is fixed once Week 2 begins.** Both pipelines build against it. Changes require agreement from both members, since either side altering it unilaterally breaks the other.

**The API response schema is fixed once Week 6 completes.** The frontend's TypeScript types mirror it. Changes after this point require corresponding frontend changes and are avoided during the final weeks.
