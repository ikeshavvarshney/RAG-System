import { apiFetch, errorMessage } from "@/lib/api";

export const METRICS = [
  {
    key: "faithfulness",
    label: "Faithfulness",
    about: "Share of the answer's claims that the retrieved passages support. A low score means the answer says things its sources do not.",
  },
  {
    key: "answer_relevancy",
    label: "Answer relevancy",
    about: "How directly the answer addresses the question that was asked, rather than something near it.",
  },
  {
    key: "context_precision",
    label: "Context precision",
    about: "Share of the retrieved passages that were relevant, weighted towards the top ranks. Low means retrieval brought noise along.",
  },
  {
    key: "context_recall",
    label: "Context recall",
    about: "Share of the reference answer's facts that appear in the retrieved passages. Low means retrieval missed the evidence.",
  },
] as const;

export type MetricKey = (typeof METRICS)[number]["key"];
export type Scores = Record<MetricKey, number | null>;
export type AnswerSource = "corpus" | "web" | "mixed" | "none";

export const SOURCES: { key: AnswerSource; label: string; swatch: string }[] = [
  { key: "corpus", label: "Corpus", swatch: "bg-series-corpus" },
  { key: "web", label: "Web", swatch: "bg-series-web" },
  { key: "mixed", label: "Corpus and web", swatch: "bg-series-mixed" },
  { key: "none", label: "No answer", swatch: "bg-series-none" },
];

export const CATEGORY_LABELS: Record<string, string> = {
  single_hop: "Single-hop factual",
  multi_hop: "Multi-hop",
  table_chart: "Table and chart",
  keyword: "Keyword-heavy",
  out_of_corpus: "Out of corpus",
};

export function categoryLabel(category: string | null): string {
  if (!category) return "Uncategorised";
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

export interface RunSummary {
  name: string;
  status: "scored" | "partial";
  updated_at: number;
  questions: number;
  judge_model: string | null;
  preset: string | null;
  scores: Scores;
  corpus_coverage_percent: number | null;
}

export interface EvalQuestion {
  id: string;
  category: string | null;
  question: string;
  reference_answer: string | null;
  answer: string | null;
  answer_source: AnswerSource | null;
  visual_dependent: boolean;
  error: string | null;
  terminated_at: string | null;
  wall_clock_sec: number | null;
  context_count: number;
  citation_count: number;
  status: string;
  scores: Scores;
  failures: string[];
  judge_calls: number | null;
  judge_seconds: number | null;
  judge_tokens: number | null;
}

export interface RunDetail {
  name: string;
  status: "scored" | "partial";
  judge_model: string | null;
  config: {
    preset: string | null;
    overrides?: Record<string, unknown>;
    effective?: Record<string, unknown>;
  } | null;
  split: {
    questions: number;
    counts: Record<AnswerSource, number>;
    corpus_coverage_percent: number;
    low_corpus_coverage: boolean;
  } | null;
  aggregate: Record<string, Partial<Scores> & { questions: number }> | null;
  judge_usage: {
    prompt_tokens: number;
    completion_tokens: number;
    http_429: number;
    scoring_seconds: number;
  } | null;
  skipped: { id: string; reason: string }[];
  questions: EvalQuestion[];
}

export interface ReferenceSummary {
  questions: number;
  per_category: Record<string, number>;
  visual_dependent: number;
  ids: string[];
  judge_configured: boolean;
}

export interface JobStatus {
  state: "idle" | "running" | "finished" | "failed" | "stopped";
  run_name?: string;
  phase?: "answering" | "scoring";
  elapsed_sec?: number;
  total?: number;
  answered?: number;
  scored?: number;
  command?: string;
  log?: string[];
  exit_code?: number;
  message?: string;
}

export interface StartRunRequest {
  run_name: string;
  limit?: number;
  preset?: "naive";
  dense_weight?: number;
  resume?: boolean;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, { cache: "no-store", ...init });
  } catch {
    throw new Error("Cannot reach the backend. Start it with uvicorn and reload this page.");
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(errorMessage(payload) ?? `Request failed: ${response.status}`);
  }
  return payload as T;
}

export const fetchReference = () => getJson<ReferenceSummary>("/eval/reference");
export const fetchRuns = async () => (await getJson<{ runs: RunSummary[] }>("/eval/runs")).runs;
export const fetchRun = (name: string) => getJson<RunDetail>(`/eval/runs/${encodeURIComponent(name)}`);
export const fetchStatus = () => getJson<JobStatus>("/eval/status");
export const stopRun = () => getJson<JobStatus>("/eval/stop", { method: "POST" });

export function startRun(request: StartRunRequest): Promise<JobStatus> {
  return getJson<JobStatus>("/eval/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function mean(values: (number | null | undefined)[]): number | null {
  const present = values.filter((value): value is number => typeof value === "number");
  if (present.length === 0) return null;
  return present.reduce((sum, value) => sum + value, 0) / present.length;
}

export function formatScore(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(2) : "n/a";
}

export function formatDelta(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (rounded === 0) return "0.00";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(2)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number") return "n/a";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

/** Mean score per metric over the scored questions in `questions`. */
export function averageScores(questions: EvalQuestion[]): Scores {
  const scored = questions.filter((question) => question.status === "ok");
  return Object.fromEntries(
    METRICS.map((metric) => [metric.key, mean(scored.map((question) => question.scores[metric.key]))]),
  ) as Scores;
}

/** Corpus-answered questions present in both runs, the only fair basis for a delta. */
export function sharedCorpusIds(a: RunDetail, b: RunDetail): Set<string> {
  const inB = new Set(
    b.questions.filter((q) => q.answer_source === "corpus" && q.status === "ok").map((q) => q.id),
  );
  return new Set(
    a.questions
      .filter((q) => q.answer_source === "corpus" && q.status === "ok" && inB.has(q.id))
      .map((q) => q.id),
  );
}
