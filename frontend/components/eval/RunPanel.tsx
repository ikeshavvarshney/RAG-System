"use client";

import { useState, type FormEvent } from "react";
import { Play, Stop } from "@/components/Icons";
import {
  formatDuration,
  startRun,
  stopRun,
  type JobStatus,
  type ReferenceSummary,
  type RunSummary,
} from "@/lib/eval";

interface Props {
  reference: ReferenceSummary | null;
  runs: RunSummary[];
  status: JobStatus;
  onStatus: (status: JobStatus) => void;
}

type Scope = "sample" | "all";
type Config = "full" | "naive" | "weight";

const CONFIGS: { value: Config; label: string; hint: string }[] = [
  {
    value: "full",
    label: "Full pipeline",
    hint: "The system as it ships: hybrid retrieval, query expansion, reranking and decomposition.",
  },
  {
    value: "naive",
    label: "Naive baseline",
    hint: "Dense-only retrieval over fixed-size chunks, with no expansion, reranking or decomposition. Needs the naive index, built once with scripts/ingest_naive.py.",
  },
  {
    value: "weight",
    label: "Fusion weight",
    hint: "The full pipeline with a different dense and sparse balance in rank fusion. This is the variable in the fusion weighting study.",
  },
];

function defaultName(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `run-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function Progress({ label, done, total }: { label: string; done: number; total: number }) {
  const fraction = total > 0 ? done / total : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-ink-2">{label}</span>
        <span className="tnum font-mono text-ink">
          {done} of {total}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-sm bg-sunken">
        <div
          className="h-full rounded-r-sm bg-accent transition-[width] duration-700 ease-out-expo"
          style={{ width: `${fraction * 100}%` }}
        />
      </div>
    </div>
  );
}

export default function RunPanel({ reference, runs, status, onStatus }: Props) {
  const [name, setName] = useState(defaultName);
  const [scope, setScope] = useState<Scope>("sample");
  const [limit, setLimit] = useState(5);
  const [config, setConfig] = useState<Config>("full");
  const [weight, setWeight] = useState(0.5);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [previousState, setPreviousState] = useState(status.state);

  if (previousState !== status.state) {
    // A finished run now exists under this name, so offer a fresh one rather than a resume.
    if (previousState === "running" && status.state === "finished") setName(defaultName());
    if (status.state !== "running") setStopping(false);
    setPreviousState(status.state);
  }

  const running = status.state === "running";
  const total = reference?.questions ?? 0;
  const count = scope === "all" ? total : Math.min(limit, total || limit);
  const exists = runs.some((run) => run.name === name);
  const nameValid = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name);
  const activeConfig = CONFIGS.find((option) => option.value === config)!;

  const command = [
    "python eval/run_eval.py",
    `--run-name ${name || "<name>"}`,
    scope === "sample" ? `--limit ${limit}` : "",
    config === "naive" ? "--preset naive" : "",
    config === "weight" ? `--set FUSION_DENSE_WEIGHT=${weight}` : "",
    exists ? "--resume" : "",
  ]
    .filter(Boolean)
    .join(" ");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!nameValid || running || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      onStatus(
        await startRun({
          run_name: name,
          limit: scope === "sample" ? limit : undefined,
          preset: config === "naive" ? "naive" : undefined,
          dense_weight: config === "weight" ? weight : undefined,
          resume: exists || undefined,
        }),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleStop() {
    setStopping(true);
    try {
      onStatus(await stopRun());
    } catch (caught) {
      setStopping(false);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  const field =
    "w-full rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-sm text-ink outline-none transition-colors hover:border-ink-2 focus:border-accent disabled:opacity-50";

  return (
    <div className="grid gap-x-10 gap-y-8 lg:grid-cols-2">
      <form onSubmit={handleSubmit} className="min-w-0 space-y-5 rounded-lg border border-line bg-raised p-5">
        <div>
          <label htmlFor="run-name" className="text-xs font-medium text-ink-2">
            Run name
          </label>
          <input
            id="run-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={running}
            spellCheck={false}
            className={`${field} mt-1.5 font-mono`}
          />
          {!nameValid && (
            <p className="mt-1 text-xs text-bad">
              Use letters, digits, hyphens and underscores only, starting with a letter or digit.
            </p>
          )}
          {nameValid && exists && (
            <p className="mt-1 text-xs text-warn">
              This run already has results, so it will be resumed: finished questions are kept and
              only the rest are run and scored.
            </p>
          )}
        </div>

        <fieldset disabled={running}>
          <legend className="text-xs font-medium text-ink-2">Questions</legend>
          <div className="mt-1.5 space-y-2 text-sm">
            <label className="flex flex-wrap items-center gap-2">
              <input
                type="radio"
                name="scope"
                checked={scope === "sample"}
                onChange={() => setScope("sample")}
                className="accent-accent"
              />
              The first
              <input
                type="number"
                min={1}
                max={total || 500}
                value={limit}
                onChange={(event) => setLimit(Math.max(1, Number(event.target.value) || 1))}
                onFocus={() => setScope("sample")}
                aria-label="Number of questions"
                className="tnum w-16 rounded-md border border-line-strong bg-raised px-2 py-1 font-mono text-sm text-ink outline-none hover:border-ink-2 focus:border-accent disabled:opacity-50"
              />
              questions, as a trial
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="scope"
                checked={scope === "all"}
                onChange={() => setScope("all")}
                className="accent-accent"
              />
              The whole reference set{total > 0 && ` (${total} questions)`}
            </label>
          </div>
        </fieldset>

        <fieldset disabled={running}>
          <legend className="text-xs font-medium text-ink-2">Configuration</legend>
          <div className="mt-1.5 grid grid-cols-3 gap-0.5 rounded-md border border-line bg-sunken p-0.5">
            {CONFIGS.map((option) => (
              <label
                key={option.value}
                className={`flex cursor-pointer items-center justify-center rounded-[5px] px-1.5 py-1 text-center text-[13px] leading-tight transition-colors has-focus-visible:outline-2 has-focus-visible:outline-accent ${
                  config === option.value
                    ? "bg-raised font-medium text-ink shadow-[0_1px_2px_rgb(16_19_24/0.08)]"
                    : "text-muted hover:text-ink"
                }`}
              >
                <input
                  type="radio"
                  name="config"
                  checked={config === option.value}
                  onChange={() => setConfig(option.value)}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted">{activeConfig.hint}</p>
          {config === "weight" && (
            <label className="mt-3 block text-xs text-ink-2">
              <span className="flex items-baseline justify-between">
                Dense weight
                <span className="tnum font-mono text-ink">
                  {weight.toFixed(2)} dense, {(1 - weight).toFixed(2)} sparse
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.25}
                value={weight}
                onChange={(event) => setWeight(Number(event.target.value))}
                className="mt-2 w-full accent-accent"
              />
            </label>
          )}
        </fieldset>

        {reference && !reference.judge_configured && (
          <p role="alert" className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">
            No judge key is configured. Add GROQ_EVAL_API_KEYS to backend/.env, then restart the
            backend.
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-xs text-bad">
            {error}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="submit"
            disabled={running || submitting || !nameValid || (reference !== null && !reference.judge_configured)}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:bg-sunken disabled:text-muted"
          >
            <Play width="15" height="15" />
            {submitting ? "Starting" : exists ? "Resume run" : `Run ${count} question${count === 1 ? "" : "s"}`}
          </button>
          <p className="tnum text-xs text-muted">
            Roughly {Math.max(2, count * 2)} minutes: about a minute to answer and a minute to
            score each question.
          </p>
        </div>
      </form>

      <div className="min-w-0">
        <h3 className="text-xs font-medium text-ink-2">Same run from a terminal</h3>
        <pre className="mt-1.5 overflow-x-auto rounded-md border border-line bg-raised px-3 py-2.5 font-mono text-xs leading-relaxed text-ink">
          {command}
        </pre>
        <p className="mt-1.5 text-xs text-muted">Run it from the repository root with the backend virtualenv active.</p>

        <div className="mt-6" aria-live="polite">
          {status.state === "idle" && (
            <p className="text-sm text-ink-2">
              No run has been started from this page since the backend came up.
            </p>
          )}

          {status.state !== "idle" && (
            <div>
              <div className="flex items-center gap-3">
                <h3 className="flex items-center gap-2 text-sm font-medium">
                  {running && <span className="size-1.5 animate-pulse rounded-full bg-accent" />}
                  <span className="font-mono">{status.run_name}</span>
                  <span className="font-normal text-ink-2">
                    {running
                      ? status.phase === "scoring"
                        ? "scoring answers"
                        : "answering questions"
                      : status.state}
                  </span>
                </h3>
                <span className="tnum ml-auto font-mono text-xs text-muted">
                  {formatDuration(status.elapsed_sec)}
                </span>
                {running && (
                  <button
                    type="button"
                    onClick={handleStop}
                    disabled={stopping}
                    className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-raised px-2.5 py-1 text-xs text-ink hover:bg-sunken disabled:opacity-50"
                  >
                    <Stop width="14" height="14" />
                    {stopping ? "Stopping" : "Stop"}
                  </button>
                )}
              </div>

              <div className="mt-3 space-y-3">
                <Progress label="Answered by the pipeline" done={status.answered ?? 0} total={status.total ?? 0} />
                <Progress label="Scored by the judge" done={status.scored ?? 0} total={status.total ?? 0} />
              </div>

              {status.message && (
                <p
                  className={`mt-3 text-xs leading-relaxed ${
                    status.state === "failed" ? "text-bad" : "text-ink-2"
                  }`}
                >
                  {status.message}
                </p>
              )}

              {status.log && status.log.length > 0 && (
                <details className="mt-3" open={status.state === "failed"}>
                  <summary className="cursor-pointer text-xs text-ink-2 hover:text-ink">
                    Log, last {status.log.length} lines
                  </summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-line bg-raised px-3 py-2.5 font-mono text-[11px] leading-relaxed text-ink-2">
                    {status.log.join("\n")}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
