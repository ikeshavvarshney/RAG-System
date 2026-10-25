"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { METRICS, fetchRuns, formatScore, type RunSummary } from "@/lib/eval";
import { listCorpusDocuments, type CorpusDocument } from "@/lib/viewer";

const RETRY_MS = 5000;

type State =
  | { kind: "loading" }
  | { kind: "offline" }
  | { kind: "ready"; corpus: CorpusDocument[]; run: RunSummary | null };

export default function SystemSnapshot() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    Promise.all([listCorpusDocuments(), fetchRuns().catch(() => [] as RunSummary[])])
      .then(([corpus, runs]) => {
        if (!active) return;
        const scored = runs.filter((run) => run.status === "scored").sort((a, b) => b.updated_at - a.updated_at);
        setState({ kind: "ready", corpus, run: scored[0] ?? null });
      })
      .catch(() => {
        if (!active) return;
        setState({ kind: "offline" });
        retry = setTimeout(() => setAttempt((count) => count + 1), RETRY_MS);
      });
    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, [attempt]);

  return (
    <aside className="rounded-lg border border-line bg-raised" aria-label="Live system state">
      <h2 className="border-b border-line px-4 py-2.5 text-xs font-semibold text-ink">This deployment</h2>

      {state.kind === "loading" && (
        <div className="space-y-3 p-4" aria-hidden="true">
          <div className="skeleton h-4 w-2/3" />
          <div className="skeleton h-4 w-1/2" />
          <div className="skeleton h-16 w-full" />
        </div>
      )}

      {state.kind === "offline" && (
        <p className="flex gap-2 p-4 text-sm leading-relaxed text-ink-2" role="status">
          <span className="mt-2 size-1.5 shrink-0 animate-pulse rounded-full bg-warn" />
          The backend is not responding yet. Start it with uvicorn; this panel retries on its own.
        </p>
      )}

      {state.kind === "ready" && (
        <>
          <dl className="grid grid-cols-2 border-b border-line">
            <div className="border-r border-line px-4 py-3">
              <dt className="text-xs text-muted">Documents</dt>
              <dd className="tnum mt-0.5 font-mono text-lg font-medium">{state.corpus.length}</dd>
            </div>
            <div className="px-4 py-3">
              <dt className="text-xs text-muted">Passages</dt>
              <dd className="tnum mt-0.5 font-mono text-lg font-medium">
                {state.corpus.reduce((sum, doc) => sum + doc.chunk_count, 0).toLocaleString("en-US")}
              </dd>
            </div>
          </dl>

          {state.run ? (
            <div className="px-4 py-3">
              <p className="flex items-baseline justify-between gap-2 text-xs">
                <span className="text-muted">Latest scored run</span>
                <span className="truncate font-mono text-ink-2" title={state.run.name}>{state.run.name}</span>
              </p>
              <ul className="mt-2.5 space-y-2">
                {METRICS.map((metric) => {
                  const value = state.run!.scores[metric.key];
                  return (
                    <li key={metric.key} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 text-xs">
                      <span className="text-ink-2">{metric.label}</span>
                      <span className="tnum font-mono text-ink">{formatScore(value)}</span>
                      <span className="col-span-2 block h-1 overflow-hidden rounded-full bg-sunken">
                        {typeof value === "number" && (
                          <span className="block h-full rounded-full bg-accent" style={{ width: `${value * 100}%` }} />
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
              <Link href="/evaluation" className="mt-3 inline-block text-xs font-medium text-accent hover:underline">
                Full results
              </Link>
            </div>
          ) : (
            <p className="px-4 py-3 text-xs leading-relaxed text-ink-2">
              No evaluation run has been scored yet.{" "}
              <Link href="/evaluation#run" className="font-medium text-accent hover:underline">
                Start one
              </Link>
            </p>
          )}
        </>
      )}
    </aside>
  );
}
