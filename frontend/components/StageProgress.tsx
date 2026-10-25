import type { StageEvent } from "@/lib/api";

const LABELS: Record<string, string> = {
  guardrail: "Input checks",
  greeting: "Greeting check",
  guardrail_llm: "Safety check",
  greeting_llm: "Intent check",
  history: "Conversation context",
  cache: "Answer cache",
  decomposition: "Question splitting",
  expansion: "Query expansion",
  retrieval: "Hybrid retrieval",
  fusion: "Rank fusion",
  sufficiency: "Sufficiency check",
  web_search: "Web search",
  rerank: "Reranking",
  generation: "Answer generation",
  citations: "Citation filtering",
  verification: "Fact check",
  output_guardrail: "Output checks",
};

export interface StageRow {
  key: string;
  stage: string;
  subQuestion?: number;
  status: StageEvent["status"];
  durationMs?: number;
}

/** Fold one stage event into the ordered row list. */
export function applyStageEvent(rows: StageRow[], event: StageEvent): StageRow[] {
  const key = `${event.stage}:${event.sub_question ?? ""}`;
  const row: StageRow = {
    key,
    stage: event.stage,
    subQuestion: event.sub_question,
    status: event.status,
    durationMs: event.duration_ms,
  };
  const index = rows.findIndex((existing) => existing.key === key);
  if (index === -1) return [...rows, row];
  const next = rows.slice();
  next[index] = row;
  return next;
}

function formatMs(ms: number): string {
  if (ms < 1) return "<1 ms";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

const MARK = {
  started: <span className="size-1.5 animate-pulse rounded-full bg-accent" />,
  completed: <span className="size-1.5 rounded-full bg-good" />,
  failed: <span className="size-1.5 rounded-full bg-bad" />,
};

export default function StageProgress({ rows }: { rows: StageRow[] }) {
  return (
    <ol className="space-y-1">
      {rows.map((row) => (
        <li key={row.key} className="flex items-center gap-2.5 text-xs">
          <span className="flex w-2 justify-center">{MARK[row.status]}</span>
          <span
            className={
              row.status === "started"
                ? "font-medium text-ink"
                : row.status === "failed"
                  ? "text-bad"
                  : "text-ink-2"
            }
          >
            {LABELS[row.stage] ?? row.stage}
            {row.subQuestion !== undefined && (
              <span className="font-normal text-muted"> (part {row.subQuestion + 1})</span>
            )}
          </span>
          {row.durationMs !== undefined && (
            <span className="tnum ml-auto font-mono text-[11px] text-muted">
              {formatMs(row.durationMs)}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
