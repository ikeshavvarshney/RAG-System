"use client";

import { useState } from "react";
import Citations, { AnswerBadges, AnswerText, QuestionNotes } from "@/components/Citations";
import { Check, Copy, Retry } from "@/components/Icons";
import StageProgress, { type StageRow } from "@/components/StageProgress";
import UsagePanel from "@/components/UsagePanel";
import type { QueryResponse, UsageEvent } from "@/lib/api";

export interface AnswerState {
  stages: StageRow[];
  usage: UsageEvent[];
  result?: QueryResponse;
  error?: string;
}

const action =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ink-2 transition-colors hover:bg-sunken hover:text-ink";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button type="button" onClick={copy} className={action}>
      {copied ? <Check className="text-good" width="14" height="14" /> : <Copy width="14" height="14" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export default function AnswerCard({
  id,
  state,
  onRetry,
}: {
  id: string;
  state: AnswerState;
  onRetry?: () => void;
}) {
  const { stages, usage, result, error } = state;
  const running = !result && !error;
  const totalMs = stages.reduce((sum, row) => sum + (row.durationMs ?? 0), 0);
  const current = [...stages].reverse().find((row) => row.status === "started");

  return (
    <article>
      {running && (
        <div className="max-w-sm rounded-lg border border-line px-3.5 py-3" role="status" aria-live="polite">
          <p className="mb-2.5 flex items-center gap-2 text-xs font-medium text-ink">
            <span className="size-1.5 animate-pulse rounded-full bg-accent" />
            {stages.length === 0 ? "Starting" : current ? "Running the pipeline" : "Finishing"}
          </p>
          <StageProgress rows={stages} />
        </div>
      )}

      {error && (
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-sm text-bad">
          <span className="min-w-0 flex-1">{error}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-md border border-bad/30 bg-raised px-2.5 py-1 text-xs font-medium text-bad hover:bg-bad-soft"
            >
              <Retry width="14" height="14" />
              Try again
            </button>
          )}
        </div>
      )}

      {result && (
        <>
          <QuestionNotes result={result} />
          <AnswerText text={result.answer} citations={result.citations} anchor={`cite-${id}`} />
          <AnswerBadges result={result} />
          <Citations citations={result.citations} anchor={`cite-${id}`} />

          <div className="-ml-2 mt-3 flex flex-wrap items-start gap-x-1 gap-y-1">
            <CopyButton text={result.answer} />
            <UsagePanel events={usage} buttonClass={action} />
            {stages.length > 0 && (
              <details className="group">
                <summary className={`${action} cursor-pointer list-none [&::-webkit-details-marker]:hidden`}>
                  <span className="tnum">
                    {stages.length} stages{totalMs > 0 && ` · ${(totalMs / 1000).toFixed(1)} s`}
                  </span>
                </summary>
                <div className="mt-2 w-72 rounded-md border border-line px-3 py-2.5">
                  <StageProgress rows={stages} />
                </div>
              </details>
            )}
          </div>
        </>
      )}
    </article>
  );
}
