"use client";

import { Fragment, useMemo, useState } from "react";
import { Chevron } from "@/components/Icons";
import {
  METRICS,
  SOURCES,
  averageScores,
  categoryLabel,
  formatDuration,
  formatScore,
  type EvalQuestion,
  type MetricKey,
} from "@/lib/eval";

const SHORT: Record<MetricKey, string> = {
  faithfulness: "Faithful",
  answer_relevancy: "Relevancy",
  context_precision: "Precision",
  context_recall: "Recall",
};

function tint(value: number | null): string | undefined {
  if (typeof value !== "number") return undefined;
  return `rgb(29 91 214 / ${(0.02 + value * 0.13).toFixed(3)})`;
}

function ScoreGrid({ scores }: { scores: Record<MetricKey, number | null> }) {
  return (
    <dl className="mt-2 grid grid-cols-4 gap-px overflow-hidden rounded border border-line bg-line">
      {METRICS.map((metric) => {
        const value = scores[metric.key];
        return (
          <div key={metric.key} className="bg-raised px-1.5 py-1" style={{ backgroundColor: tint(value) }}>
            <dt className="text-[10px] text-muted">{SHORT[metric.key]}</dt>
            <dd className="tnum font-mono text-xs">
              {typeof value === "number" ? value.toFixed(2) : <span className="text-muted">n/a</span>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function ScoreCell({ value }: { value: number | null }) {
  return (
    <td className="tnum px-2 py-2 text-right font-mono text-xs" style={{ backgroundColor: tint(value) }}>
      {typeof value === "number" ? value.toFixed(2) : <span className="text-muted">n/a</span>}
    </td>
  );
}

export function CategoryTable({ questions }: { questions: EvalQuestion[] }) {
  const rows = useMemo(() => {
    const groups = new Map<string, EvalQuestion[]>();
    for (const question of questions) {
      const key = question.category ?? "uncategorised";
      groups.set(key, [...(groups.get(key) ?? []), question]);
    }
    return [...groups.entries()].map(([category, members]) => ({
      category,
      count: members.length,
      scored: members.filter((member) => member.status === "ok").length,
      scores: averageScores(members),
    }));
  }, [questions]);

  return (
    <>
    <ul className="space-y-2 sm:hidden">
      {rows.map((row) => (
        <li key={row.category} className="rounded-lg border border-line bg-raised p-3">
          <p className="flex items-baseline justify-between gap-2 text-sm">
            {categoryLabel(row.category)}
            <span className="tnum font-mono text-xs text-muted">
              {row.scored} of {row.count}
            </span>
          </p>
          <ScoreGrid scores={row.scores} />
        </li>
      ))}
    </ul>
    <div className="hidden overflow-x-auto rounded-lg border border-line bg-raised sm:block">
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line-strong text-left text-xs text-muted bg-surface">
            <th className="py-2 pl-4 pr-3 font-medium">Category</th>
            <th className="px-2 py-2 text-right font-normal">Scored</th>
            {METRICS.map((metric) => (
              <th key={metric.key} className="px-2 py-2 text-right font-normal" title={metric.about}>
                {SHORT[metric.key]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.category} className="border-b border-line last:border-b-0">
              <th className="py-2 pl-4 pr-3 text-left font-normal">{categoryLabel(row.category)}</th>
              <td className="tnum px-2 py-2 text-right font-mono text-xs text-ink-2">
                {row.scored} of {row.count}
              </td>
              {METRICS.map((metric) => (
                <ScoreCell key={metric.key} value={row.scores[metric.key]} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}

type SortKey = "id" | MetricKey;

export default function QuestionTable({ questions }: { questions: EvalQuestion[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const [category, setCategory] = useState("all");
  const [source, setSource] = useState("all");
  const [sort, setSort] = useState<SortKey>("id");

  const categories = useMemo(
    () => [...new Set(questions.map((question) => question.category ?? "uncategorised"))],
    [questions],
  );

  const visible = useMemo(() => {
    const filtered = questions.filter(
      (question) =>
        (category === "all" || (question.category ?? "uncategorised") === category) &&
        (source === "all" || question.answer_source === source),
    );
    if (sort === "id") return filtered;
    return [...filtered].sort((a, b) => (a.scores[sort] ?? 2) - (b.scores[sort] ?? 2));
  }, [questions, category, source, sort]);

  const select =
    "rounded-md border border-line-strong bg-raised px-2 py-1 text-xs text-ink outline-none hover:border-ink-2 focus:border-accent";

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-2">
        <label className="flex items-center gap-2">
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value)} className={select}>
            <option value="all">All</option>
            {categories.map((value) => (
              <option key={value} value={value}>
                {categoryLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Answered from
          <select value={source} onChange={(event) => setSource(event.target.value)} className={select}>
            <option value="all">Anywhere</option>
            {SOURCES.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Order
          <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} className={select}>
            <option value="id">Question order</option>
            {METRICS.map((metric) => (
              <option key={metric.key} value={metric.key}>
                Lowest {metric.label.toLowerCase()} first
              </option>
            ))}
          </select>
        </label>
        <span className="tnum ml-auto text-muted">
          {visible.length} of {questions.length}
        </span>
      </div>

      <ul className="mt-3 space-y-2 sm:hidden">
        {visible.length === 0 && <li className="py-4 text-center text-xs text-muted">No questions match these filters.</li>}
        {visible.map((question) => {
          const expanded = open === question.id;
          const sourceMeta = SOURCES.find((option) => option.key === question.answer_source);
          return (
            <li key={question.id} className="rounded-lg border border-line bg-raised">
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : question.id)}
                aria-expanded={expanded}
                className="block w-full p-3 text-left"
              >
                <p className={`text-sm ${expanded ? "" : "line-clamp-2"}`}>
                  <span className="tnum mr-1.5 font-mono text-xs text-muted">{question.id}</span>
                  {question.question}
                </p>
                <p className="mt-1 flex items-center gap-1.5 text-xs text-muted">
                  {sourceMeta && <span className={`size-2 rounded-xs ${sourceMeta.swatch}`} />}
                  {sourceMeta?.label ?? "n/a"} · {categoryLabel(question.category)}
                </p>
                <ScoreGrid scores={question.scores} />
              </button>
              {expanded && (
                <div className="space-y-3 border-t border-line p-3 text-sm leading-relaxed">
                  <div>
                    <h4 className="text-xs font-medium text-muted">System answer</h4>
                    <p className="mt-1 whitespace-pre-wrap">{question.answer || "No answer was produced."}</p>
                  </div>
                  <div>
                    <h4 className="text-xs font-medium text-muted">Reference answer</h4>
                    <p className="mt-1 whitespace-pre-wrap text-ink-2">{question.reference_answer || "None recorded."}</p>
                  </div>
                  {(question.error || question.failures.length > 0) && (
                    <p role="alert" className="text-xs text-bad">
                      {[question.error, ...question.failures].filter(Boolean).join(" ")}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <div className="mt-3 hidden overflow-x-auto rounded-lg border border-line bg-raised sm:block">
        <table className="w-full min-w-[44rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line-strong text-left text-xs text-muted bg-surface">
              <th className="w-9 py-2 pl-3 font-normal">
                <span className="sr-only">Expand</span>
              </th>
              <th className="py-2 pr-3 font-normal">Question</th>
              <th className="px-2 py-2 font-normal">Source</th>
              {METRICS.map((metric) => (
                <th key={metric.key} className="px-2 py-2 text-right font-normal" title={metric.about}>
                  {SHORT[metric.key]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-xs text-muted">
                  No questions match these filters.
                </td>
              </tr>
            )}
            {visible.map((question) => {
              const expanded = open === question.id;
              const sourceMeta = SOURCES.find((option) => option.key === question.answer_source);
              return (
                <Fragment key={question.id}>
                  <tr
                    onClick={() => setOpen(expanded ? null : question.id)}
                    className={`cursor-pointer border-b border-line align-top hover:bg-surface ${expanded ? "bg-surface" : ""}`}
                  >
                    <td className="py-2 pl-3" onClick={(event) => event.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => setOpen(expanded ? null : question.id)}
                        aria-expanded={expanded}
                        aria-label={`${expanded ? "Hide" : "Show"} details for ${question.id}`}
                        className="rounded p-0.5 text-muted hover:bg-sunken hover:text-ink"
                      >
                        <Chevron
                          width="14"
                          height="14"
                          className={`transition-transform duration-200 ${expanded ? "rotate-90" : ""}`}
                        />
                      </button>
                    </td>
                    <td className="max-w-md py-2 pr-3">
                      <p className={expanded ? "" : "line-clamp-1"}>
                        <span className="tnum mr-2 font-mono text-xs text-muted">{question.id}</span>
                        {question.question}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">
                        {categoryLabel(question.category)}
                        {question.visual_dependent && ", needs a table or chart"}
                      </p>
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 text-xs text-ink-2">
                      {sourceMeta ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className={`size-2 rounded-xs ${sourceMeta.swatch}`} />
                          {sourceMeta.label}
                        </span>
                      ) : (
                        "n/a"
                      )}
                    </td>
                    {METRICS.map((metric) => (
                      <ScoreCell key={metric.key} value={question.scores[metric.key]} />
                    ))}
                  </tr>
                  {expanded && (
                    <tr className="border-b border-line bg-surface">
                      <td />
                      <td colSpan={6} className="py-4 pr-4">
                        <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
                          <div>
                            <h4 className="text-xs font-medium text-muted">System answer</h4>
                            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">
                              {question.answer || "No answer was produced."}
                            </p>
                          </div>
                          <div>
                            <h4 className="text-xs font-medium text-muted">Reference answer</h4>
                            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-2">
                              {question.reference_answer || "None recorded."}
                            </p>
                          </div>
                        </div>
                        <p className="tnum mt-4 text-xs text-muted">
                          {question.context_count} passages retrieved, {question.citation_count} cited.
                          Pipeline {formatDuration(question.wall_clock_sec)}
                          {question.judge_calls !== null &&
                            `, judge ${question.judge_calls} calls in ${formatDuration(question.judge_seconds)}`}
                          .
                        </p>
                        {(question.error || question.failures.length > 0) && (
                          <p role="alert" className="mt-2 text-xs text-bad">
                            {[question.error, ...question.failures].filter(Boolean).join(" ")}
                          </p>
                        )}
                        {question.status === "unscored" && !question.error && (
                          <p className="mt-2 text-xs text-warn">The judge has not scored this question.</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted">
        A {formatScore(null)} score means the judge could not produce that metric for the question.
      </p>
    </div>
  );
}
