import Citations, { AnswerBadges, AnswerText, QuestionNotes } from "@/components/Citations";
import StageProgress, { type StageRow } from "@/components/StageProgress";
import UsagePanel from "@/components/UsagePanel";
import type { QueryResponse, UsageEvent } from "@/lib/api";

export interface AnswerState {
  stages: StageRow[];
  usage: UsageEvent[];
  result?: QueryResponse;
  error?: string;
}

export default function AnswerCard({ id, state }: { id: string; state: AnswerState }) {
  const { stages, usage, result, error } = state;
  const running = !result && !error;

  return (
    <div className="w-full max-w-[85%] rounded-2xl bg-neutral-100 px-4 py-3 text-sm leading-relaxed text-neutral-800">
      {running && (
        <>
          <p className="mb-2 text-xs font-medium text-neutral-500">Working...</p>
          <StageProgress rows={stages} />
        </>
      )}

      {error && <p className="text-red-600">{error}</p>}

      {result && (
        <>
          <QuestionNotes result={result} />
          <AnswerText text={result.answer} anchor={`cite-${id}`} />
          <AnswerBadges result={result} />
          <Citations citations={result.citations} anchor={`cite-${id}`} />
          <UsagePanel events={usage} />
          {stages.length > 0 && (
            <details className="mt-3 border-t border-neutral-200 pt-2">
              <summary className="cursor-pointer text-xs text-neutral-500">
                Pipeline ({stages.length} stages)
              </summary>
              <div className="mt-2">
                <StageProgress rows={stages} />
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
