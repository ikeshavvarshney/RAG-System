# 5. Research Methodology

The project's research contribution is not a new algorithm. It is a controlled evaluation of two design choices that RAG implementations routinely make without measurement, conducted on a real mixed-media corpus with a documented experimental procedure.

This document sets out the experimental design in advance of running it. That ordering is deliberate: an evaluation designed after seeing results is not an evaluation.

---

## 5.1 Research Questions

### RQ1 — Fusion weighting

> In hybrid retrieval, how does the relative weighting of dense and sparse retrieval within Reciprocal Rank Fusion affect answer quality, and does hybrid retrieval measurably outperform dense-only retrieval on a mixed-domain corpus?

Hybrid retrieval is widely adopted on the reasoning that dense and sparse methods fail in complementary ways. The *degree* to which this holds, and the balance at which it is optimal, is corpus-dependent and is often left at a default. This question measures it directly.

### RQ2 — Multimodal extraction

> Does vision-language-model extraction of tables and charts produce measurably better answers than structure-aware OCR extraction, on questions whose answers reside in that visual content?

Vision-based document extraction is more expensive than OCR by a considerable margin. Whether that cost purchases a corresponding improvement in downstream answer quality — as opposed to merely producing more impressive-looking intermediate output — is an empirical question that this study addresses.

---

## 5.2 Evaluation Framework

### Metrics

Evaluation uses the RAGAS suite. Its four core metrics divide cleanly into retrieval quality and generation quality, which matters here because the two ablations intervene at different pipeline stages.

| Metric | Measures | Relevance |
|---|---|---|
| **Context precision** | Proportion of retrieved context that is relevant | Retrieval quality — primary signal for RQ1 |
| **Context recall** | Proportion of necessary information successfully retrieved | Retrieval quality — primary signal for RQ2 |
| **Faithfulness** | Whether the answer is supported by the retrieved context | Generation quality; detects fabrication |
| **Answer relevancy** | Whether the answer addresses the question asked | Generation quality |

RAGAS employs a language model as judge. This introduces measurement noise and is acknowledged as a limitation in §5.6; the alternative — human evaluation at sufficient scale — is not feasible within the project's timeframe.

### Efficiency measures

Quality metrics alone do not distinguish a configuration that improves results cheaply from one that improves them at several times the cost. The system records token consumption and elapsed time per pipeline stage as a property of its instrumentation, so both are available for every evaluation run without additional work.

| Measure | Reported as |
|---|---|
| Token consumption | Total per query, and attributed per pipeline stage |
| Latency | Elapsed time per stage, and end to end |
| Ingestion cost | Tokens and wall-clock time to construct each index |

These are reported alongside the quality metrics for each ablation configuration. Their role is secondary — the research questions concern answer quality — but a cost-quality comparison materially strengthens the recommendation each result supports.

### Reference question-answer set

A hand-constructed set of 30-50 question-answer pairs drawn from the corpus. Each entry records the question, a reference answer, the source documents and pages containing the answer, a category label, and whether the question depends on tabular or graphical content.

Reference answers are written from the source documents directly, never from system output. Evaluating a system against answers it produced would measure only self-consistency.

**Composition is a design decision, not an accident of collection.** A set composed uniformly of straightforward single-hop lookups produces near-identical scores across every configuration, yielding an experiment incapable of distinguishing between them. The distribution below is chosen so that each ablation has questions capable of separating its conditions:

| Category | Share | Purpose |
|---|---|---|
| Single-hop factual | ~40% | Baseline; answerable from one passage |
| Multi-hop / comparative | ~20% | Requires multiple passages; exercises decomposition |
| **Table and chart dependent** | ~25% | **The entire measurement basis for RQ2** |
| **Keyword-heavy** | ~10% | Exact identifiers and rare terms; **where sparse retrieval should demonstrate its value, and therefore what makes RQ1's sweep informative rather than flat** |
| Out-of-corpus | ~5% | Should trigger web fallback or an honest non-answer; tests against fabrication |

Two constraints on the table and chart questions are essential to RQ2's validity. They must be genuinely unanswerable from surrounding prose — a question whose answer also appears in a caption or nearby paragraph will be answered identically under both conditions, contributing nothing. And they must be drawn from documents deliberately selected for containing real tables and charts, which is why corpus construction (§5.3) treats chart density as a requirement rather than leaving it to chance.

### Procedure

Each configuration is evaluated by running the complete question set through the live pipeline and scoring the results. Per-question results are recorded before aggregation, so that an unexpected aggregate can be traced to the specific questions producing it.

Two procedural requirements protect the validity of every run:

**The semantic answer cache is bypassed during evaluation.** A cache hit returns an answer generated under a previous configuration. Since the ablations differ only in configuration, an active cache would return identical answers across conditions and produce a null result by construction. This is the single most consequential procedural error available in this experimental design and is guarded explicitly.

**Bug fixes precede ablation runs.** Any change altering pipeline behaviour invalidates results collected before it. The evaluation sequence is therefore: baseline run, fix defects surfaced, re-establish baseline, then run both ablations against the final pipeline.

**The baseline is executed three times** on identical input and configuration. The spread across those runs establishes the measurement noise floor, without which there is no basis for treating a small ablation delta as a real effect. The variance figure is reported alongside every result table.

**A naive baseline configuration is also evaluated** — dense-only retrieval, fixed-size chunking, no reranking, no query expansion — providing an absolute floor. Without it, every reported figure is relative to the project's own tuned configurations, and nothing establishes that the complete pipeline outperforms a conventional one.

**Results are partitioned by answer source.** Web-search fallback remains enabled during evaluation, since disabling it would measure a system different from the one that ships. Results are therefore separated into corpus-answered and web-answered questions, and **ablation deltas are computed only over corpus-answered questions**. A web-answered result reflects nothing about the retrieval configuration under test, so combining the two populations in a single metric would contaminate every reported difference. The proportion of each population is recorded per run; a shift in that proportion between configurations is itself informative.

**Sampling is deterministic.** Temperature is fixed at zero for all auxiliary model calls and low for generation, and every sampling parameter is recorded with each run. Without this, repeated runs of an identical configuration produce differing scores and no ablation delta can be separated from sampling variation.

**Judgment is performed by a different model family** from the one generating the answers. Using the generating model as its own judge invites self-preference bias, which is documented in the model-as-judge literature and inexpensive to avoid.

---

## 5.3 Corpus Construction

The corpus comprises 20-50 documents spanning PDF, DOCX, and image formats, drawn from a mixed general domain — news articles, wiki-style reference material, reports, and standalone charts and tables.

**Domain choice.** A mixed general domain was chosen over a specialised one (such as academic papers) for practical reasons: sourcing sufficient documents is faster, and licensing is clearer. The trade-off is accepted knowingly — a specialised corpus would support harder multi-hop questions, at the cost of a slower and more constrained collection process.

**Chart and table density is a construction requirement.** At least eight documents must contain genuine tables, charts, or figures. This is enforced by an automated corpus validator rather than left to judgment, for a direct methodological reason: RQ2 measures the difference between two extraction methods on visual content, and a corpus containing little visual content cannot produce a measurable difference regardless of how the extraction methods actually perform. A null result arising from insufficient corpus signal is not a finding about extraction methods, and would be indistinguishable from one at analysis time.

**Provenance.** Every document is recorded in a manifest with its source, licence, type, and a flag for whether it contains tabular or graphical content. Reported results are conditioned on a specific corpus, and the manifest is what makes that corpus describable in the paper.

**Validation.** An automated validator enforces document count, format coverage, the chart-density floor, manifest completeness, and manifest-to-filesystem consistency. It runs as a gate rather than as advice.

---

## 5.4 RQ1 — Fusion Weighting Ablation

### Design

Single-variable sweep. The dense-to-sparse weighting within Reciprocal Rank Fusion is varied; every other pipeline parameter is held constant.

| Configuration | Dense | Sparse |
|---|---|---|
| A | 1.00 | 0.00 |
| B | 0.75 | 0.25 |
| C | 0.50 | 0.50 |
| D | 0.25 | 0.75 |
| E | 0.00 | 1.00 |

The full question set is evaluated under each configuration. The endpoints matter as much as the intermediate points: configuration A is dense-only retrieval and configuration E is sparse-only, making the comparison between A and the best hybrid setting the direct test of whether hybrid retrieval justifies its additional complexity.

### Measurements

All four RAGAS metrics per configuration, plus retrieval-level diagnostics: how frequently each retriever contributed to the final top-k, and how this varies across question categories. Results are reported as a table and as a curve across the weighting spectrum.

Token consumption and per-stage latency are recorded for each configuration. Fusion weighting is not expected to alter cost materially — the same retrievers execute regardless of how their results are weighted — so a substantial cost difference between configurations would indicate a confound rather than a finding, and is worth checking for that reason.

### Anticipated outcomes

Three outcomes are possible and all are reportable:

1. **A hybrid optimum.** Some intermediate weighting outperforms both endpoints — the expected result, and the one that would justify hybrid retrieval on this corpus.
2. **Dense-only competitive.** Prose-heavy general-domain content is well suited to dense retrieval, and sparse retrieval may add little. This would be a legitimate finding and a useful caution against adopting hybrid retrieval by default.
3. **Category-dependent optimum.** Different question categories favour different weightings — plausible given that the keyword-heavy category is specifically constructed to favour sparse retrieval. This would be the most interesting outcome and would support a per-query adaptive weighting proposal in future work.

The keyword-heavy question category exists precisely so that outcome 2, if observed, reflects the corpus rather than an experimental design that gave sparse retrieval no opportunity to demonstrate its value.

---

## 5.5 RQ2 — Multimodal Extraction Ablation

### Design

Two complete indexes are constructed from the same corpus, differing only in extraction method:

- **Vision index** — tables and charts extracted by the vision-language model, with **OCR fallback disabled**. A page where vision extraction fails contributes no passage to this index.
- **OCR-only index** — the vision path disabled entirely; all visual content processed by structure-aware OCR.

The query pipeline is held identical across both conditions. Only the index changes.

**On disabling the fallback.** The production system falls back to OCR when vision extraction fails, which is correct behaviour for a deployed service. Retaining that fallback in the experimental index would, however, place OCR-derived passages inside the vision condition — making the comparison `vision with OCR fallback` against `OCR alone` rather than vision against OCR. The confound worsens as OCR quality improves, since a more capable fallback makes the two conditions more alike and shrinks the measured difference for reasons unrelated to the research question. The fallback is therefore disabled for index construction only, under an explicit flag in the evaluation harness, and the vision failure rate is reported separately.

**On the OCR baseline model.** The OCR condition must use the structure-recognition pipeline, not a vision-language model. Some OCR toolkits now ship vision-language document models alongside conventional ones; running the OCR arm through such a model would make the baseline itself vision-based and reduce the experiment to a comparison between two vision models while appearing to function normally. The model identifier is pinned in configuration, asserted by the harness, and recorded in every results file.

### Measurements

The full question set and, separately, the table-and-chart subset are evaluated against each index.

**The subset result is the finding; the full-set result is context.** Most questions in the full set do not depend on visual content, and for those the two indexes contain identical text passages and will produce identical answers. Reporting only the full-set delta would dilute a real effect across questions incapable of exhibiting it, understating the result. Both figures are reported, with the subset leading.

Supporting measurements:

- **Vision failure rate** — how frequently vision extraction failed during construction of the vision index. Since the fallback is disabled there, a failure means that page contributed nothing to the vision condition; the rate therefore bounds how much of the corpus that condition actually covers, and is reported with the deltas.
- **Extraction volume** — passage and character counts under each method.
- **Extraction cost** — token consumption and wall-clock time to construct each index. Vision extraction is substantially more expensive than OCR, so the finding is incomplete without it: the practically useful result is not whether vision extraction is better, but what quality improvement its additional cost purchases. A configuration that improves context recall by a marginal amount at several times the cost supports a different recommendation than one that improves it substantially at the same price.
- **Qualitative comparison** — the same table as rendered by each method, side by side. A single such figure conveys the nature of the difference more effectively than the aggregate metrics.

### Methodological note on the baseline

The OCR condition uses a structure-aware engine that recovers table rows and columns, rather than a reading-order engine that flattens tables into undifferentiated text.

This choice materially affects what the experiment measures. With a structure-blind baseline, a large part of any observed difference would be attributable to structured versus unstructured output — an unsurprising outcome that says little about vision extraction specifically. A structure-aware baseline makes the comparison a genuine test of whether vision-language understanding adds value beyond accurate structural transcription.

The consequence is a smaller expected effect size and a stronger claim if an effect is nonetheless observed.

---

## 5.6 Threats to Validity

Stated plainly, as each bounds what the results support.

**Single corpus, single domain.** Results are conditioned on one mixed general-domain corpus of 20-50 documents. Both findings may differ on specialised, longer, or differently-structured document collections. RQ1's outcome in particular is expected to be corpus-dependent, since the value of sparse retrieval depends on how often queries contain exact lexical matches.

**Question set size.** 30-50 questions is small for detecting modest effects. Differences of a few percentage points in aggregate metrics should not be over-interpreted; per-category breakdowns are reported alongside aggregates for this reason.

**Model-as-judge measurement noise.** RAGAS metrics are produced by a language model evaluating outputs. This introduces variance absent from exact-match metrics, and may correlate with properties of the answers being judged. Two measures reduce but do not eliminate the concern: the judge is drawn from a different model family than the generator, avoiding self-preference bias; and the baseline is run three times to quantify the residual variance.

**No corpus deletion, and a frozen corpus.** The corpus is fixed after initial indexing, so results describe a static collection. Behaviour under incremental corpus growth — index drift, embedding staleness — is untested and outside the scope of these findings.

**Single embedding model.** Retrieval quality throughout is conditioned on one embedding model, chosen under hardware constraints rather than for benchmark performance. A stronger embedding model would plausibly shift the baseline and could alter RQ1's optimum, since improving dense retrieval changes the value that sparse retrieval adds.

**Author-constructed question set.** Questions were written by the project members, who are familiar with both the corpus and the system. Question selection may unintentionally favour content the system handles well. The category quotas in §5.2 partially mitigate this by requiring representation of categories chosen for their difficulty rather than their convenience.

**No comparison against external systems.** The project measures its own configurations against one another, not against other RAG implementations. The results support claims about these design choices in this system; they do not support claims of superiority over other systems.

---

## 5.7 Reproducibility

The following are preserved and reported so that the results can be independently examined:

- The corpus manifest, with per-document provenance and licence.
- The reference question-answer set in full.
- The evaluation harness, parameterised by configuration.
- Raw per-question results for every run, retained prior to aggregation.
- Per-stage token consumption and timing for every run.
- Exact model identifiers and all configuration parameters for each run.

The corpus itself is version-controlled with the project, so both team members and any reviewer evaluate against identical data.
