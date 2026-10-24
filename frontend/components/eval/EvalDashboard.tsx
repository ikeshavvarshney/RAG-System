"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import HowItWorks from "@/components/eval/HowItWorks";
import QuestionTable, { CategoryTable } from "@/components/eval/QuestionTable";
import RunPanel from "@/components/eval/RunPanel";
import { MetricBars, RunFacts, SourceSplit } from "@/components/eval/Scoreboard";
import {
  METRICS,
  averageScores,
  fetchReference,
  fetchRun,
  fetchRuns,
  fetchStatus,
  sharedCorpusIds,
  type AnswerSource,
  type JobStatus,
  type ReferenceSummary,
  type RunDetail,
  type RunSummary,
  type Scores,
} from "@/lib/eval";

const POLL_MS = 3000;
const RETRY_MS = 3000;

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Section({ id, title, lead, children }: { id: string; title: string; lead?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="mt-16 scroll-mt-6">
      <h2 className="font-serif text-2xl font-medium tracking-tight">{title}</h2>
      {lead && <p className="mt-2 max-w-[68ch] text-sm leading-relaxed text-ink-2">{lead}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}

export default function EvalDashboard() {
  const [reference, setReference] = useState<ReferenceSummary | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [compareTo, setCompareTo] = useState("");
  const [run, setRun] = useState<RunDetail | null>(null);
  const [other, setOther] = useState<RunDetail | null>(null);
  const [status, setStatus] = useState<JobStatus>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const wasRunning = useRef(false);

  const applyRuns = useCallback((fetched: RunSummary[], prefer?: string) => {
    setRuns(fetched);
    setSelected((current) => {
      if (prefer && fetched.some((item) => item.name === prefer)) return prefer;
      if (current && fetched.some((item) => item.name === current)) return current;
      return fetched.find((item) => item.status === "scored")?.name ?? fetched[0]?.name ?? null;
    });
  }, []);

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    Promise.all([fetchReference(), fetchStatus(), fetchRuns()])
      .then(([fetchedReference, fetchedStatus, fetchedRuns]) => {
        if (!active) return;
        setReference(fetchedReference);
        setStatus(fetchedStatus);
        applyRuns(fetchedRuns);
        setWaiting(false);
      })
      .catch(() => {
        if (!active) return;
        setWaiting(true);
        retry = setTimeout(() => setAttempt((count) => count + 1), RETRY_MS);
      });
    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, [applyRuns, attempt]);

  useEffect(() => {
    if (status.state !== "running") return;
    const timer = setInterval(() => {
      fetchStatus().then(setStatus).catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [status.state]);

  useEffect(() => {
    const running = status.state === "running";
    if (wasRunning.current && !running) {
      const finished = status.run_name;
      fetchRuns()
        .then((fetched) => applyRuns(fetched, finished))
        .catch(() => undefined);
    }
    wasRunning.current = running;
  }, [status.state, status.run_name, applyRuns]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    fetchRun(selected)
      .then((detail) => {
        if (active) setRun(detail);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      active = false;
    };
  }, [selected, runs]);

  useEffect(() => {
    if (!compareTo) return;
    let active = true;
    fetchRun(compareTo)
      .then((detail) => {
        if (active) setOther(detail);
      })
      .catch(() => {
        if (active) setOther(null);
      });
    return () => {
      active = false;
    };
  }, [compareTo]);

  const current = run && run.name === selected ? run : null;
  const baseline = other && other.name === compareTo && compareTo !== selected ? other : null;

  const view = useMemo(() => {
    if (!current) return null;
    const scored = current.questions.filter((question) => question.status === "ok");
    const computed = averageScores(current.questions);
    const overall = current.aggregate?.overall;
    const scores = Object.fromEntries(
      METRICS.map((metric) => [metric.key, overall?.[metric.key] ?? computed[metric.key]]),
    ) as Scores;

    const counts: Record<AnswerSource, number> = { corpus: 0, web: 0, mixed: 0, none: 0 };
    for (const question of current.questions) {
      if (question.answer_source) counts[question.answer_source] += 1;
    }

    let comparison = null;
    if (baseline) {
      const shared = sharedCorpusIds(current, baseline);
      comparison = {
        name: baseline.name,
        shared: shared.size,
        baseScores: averageScores(current.questions.filter((question) => shared.has(question.id))),
        scores: averageScores(baseline.questions.filter((question) => shared.has(question.id))),
      };
    }
    return { scored: scored.length, scores, counts, comparison };
  }, [current, baseline]);

  const select =
    "rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-sm text-ink hover:border-ink";
  const summary = runs?.find((item) => item.name === selected);

  return (
    <div className="mx-auto max-w-6xl px-4 pb-24 pt-10 sm:px-6 sm:pt-14">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
        <div className="max-w-2xl">
          <h1 className="font-serif text-4xl font-medium tracking-tight">Evaluation</h1>
          <p className="mt-3 text-sm leading-relaxed text-ink-2">
            How well the system answers a fixed set of questions with known answers, scored with
            RAGAS by a separate judge model.
          </p>
        </div>

        {runs && runs.length > 0 && (
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-ink-2">
              Run
              <select
                value={selected ?? ""}
                onChange={(event) => setSelected(event.target.value)}
                className={`${select} mt-1 block font-mono`}
              >
                {runs.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                    {item.status === "partial" ? " (unfinished)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {runs.length > 1 && (
              <label className="text-xs text-ink-2">
                Compare with
                <select
                  value={compareTo}
                  onChange={(event) => setCompareTo(event.target.value)}
                  className={`${select} mt-1 block font-mono`}
                >
                  <option value="">None</option>
                  {runs
                    .filter((item) => item.name !== selected && item.status === "scored")
                    .map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-8 rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-sm text-bad">
          {error}
        </p>
      )}

      {!error && runs === null && (
        <p className="mt-12 flex max-w-xl gap-2.5 text-sm leading-relaxed text-ink-2" role="status">
          {waiting && <span className="mt-2 size-1.5 shrink-0 animate-pulse rounded-full bg-warn" />}
          {waiting
            ? "Waiting for the backend at localhost:8000. It can take a minute to start, and this page keeps checking on its own."
            : "Loading runs"}
        </p>
      )}

      {runs !== null && runs.length === 0 && (
        <div className="mt-12 max-w-xl border-l border-line-strong pl-4">
          <h2 className="font-serif text-xl font-medium tracking-tight">No evaluation has been run yet</h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            Start with a trial of a few questions below. It takes a couple of minutes per question,
            and the scores appear here when it finishes.
          </p>
        </div>
      )}

      {current && view && (
        <>
          {summary && (
            <p className="tnum mt-6 text-xs text-muted">
              {current.questions.length} question{current.questions.length === 1 ? "" : "s"}, last
              updated {when(summary.updated_at)}
              {current.status === "partial" && ". This run is unfinished, so scores cover only what was judged so far."}
            </p>
          )}

          <div className="mt-8 grid gap-x-12 gap-y-10 *:min-w-0 lg:grid-cols-[1fr_20rem]">
            <MetricBars
              key={current.name}
              run={current}
              scores={view.scores}
              scoredCount={view.scored}
              comparison={view.comparison}
            />
            <div className="space-y-8 lg:pt-12">
              <SourceSplit counts={view.counts} total={current.questions.length} />
              <RunFacts run={current} />
              {current.split?.low_corpus_coverage && (
                <p className="text-xs leading-relaxed text-warn">
                  Fewer than 80% of questions were answered from the corpus, so comparisons between
                  runs rest on a small sample.
                </p>
              )}
            </div>
          </div>

          <Section
            id="categories"
            title="By question category"
            lead="An average hides where a configuration helps. Each category was chosen to stress a different part of the pipeline."
          >
            <CategoryTable questions={current.questions} />
          </Section>

          <Section
            id="questions"
            title="Every question"
            lead="Open a row to read the answer beside the reference answer it was judged against."
          >
            <QuestionTable key={current.name} questions={current.questions} />
          </Section>
        </>
      )}

      <Section
        id="run"
        title="Run an evaluation"
        lead="This starts the harness on the backend machine. Results are saved after every question, and the scores load here when the run ends."
      >
        <RunPanel reference={reference} runs={runs ?? []} status={status} onStatus={setStatus} />
      </Section>

      <Section id="method" title="How evaluation works">
        <HowItWorks reference={reference} />
      </Section>
    </div>
  );
}
