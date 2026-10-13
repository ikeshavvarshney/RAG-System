"use client";

import { useState } from "react";
import Citations, { AnswerBadges, AnswerText, QuestionNotes } from "@/components/Citations";
import { Check, Copy } from "@/components/Icons";
import StageProgress, { type StageRow } from "@/components/StageProgress";
import UsagePanel from "@/components/UsagePanel";
import type { QueryResponse, UsageEvent } from "@/lib/api";

export interface AnswerState {
  stages: StageRow[];
  usage: UsageEvent[];
  result?: QueryResponse;
  error?: string;
}

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
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1.5 text-xs text-ink-2 hover:text-ink"
    >
      {copied ? <Check className="text-good" /> : <Copy />}
      {copied ? "Copied" : "Copy answer"}
    </button>
  );
}

export default function AnswerCard({ id, state }: { id: string; state: AnswerState }) {
  const { stages, usage, result, error } = state;
  const running = !result && !error;
  const totalMs = stages.reduce((sum, row) => sum + (row.durationMs ?? 0), 0);

  return (
    <article className="animate-rise">
      {running && (
        <div className="max-w-sm" role="status" aria-live="polite">
          <p className="mb-2.5 text-xs font-medium text-ink-2">
            {stages.length === 0 ? "Starting" : "Working through the pipeline"}
          </p>
          <StageProgress rows={stages} />
        </div>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-sm text-bad">
          {error}
        </p>
      )}

      {result && (
        <>
          <QuestionNotes result={result} />
          <AnswerText text={result.answer} anchor={`cite-${id}`} />
          <AnswerBadges result={result} />
          <Citations citations={result.citations} anchor={`cite-${id}`} />

          <div className="mt-3 flex flex-wrap items-start gap-x-5 gap-y-2">
            <CopyButton text={result.answer} />
            <UsagePanel events={usage} />
            {stages.length > 0 && (
              <details>
                <summary className="cursor-pointer list-none text-xs text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
                  {stages.length} pipeline stages
                  {totalMs > 0 && <span className="tnum">, {(totalMs / 1000).toFixed(1)} s</span>}
                </summary>
                <div className="mt-2 w-64">
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
