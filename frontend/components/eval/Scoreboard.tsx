import {
  METRICS,
  SOURCES,
  formatDelta,
  formatDuration,
  formatScore,
  type AnswerSource,
  type RunDetail,
  type Scores,
} from "@/lib/eval";

const number = new Intl.NumberFormat("en-US");

interface Props {
  run: RunDetail;
  scores: Scores;
  scoredCount: number;
  comparison: { name: string; scores: Scores; baseScores: Scores; shared: number } | null;
}

export function MetricBars({ run, scores, scoredCount, comparison }: Props) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold tracking-tight">Scores</h2>
        <p className="tnum text-xs text-muted">
          Mean over {scoredCount} scored question{scoredCount === 1 ? "" : "s"}, 0 to 1, higher is
          better
        </p>
      </div>

      <ul className="mt-3 overflow-hidden rounded-lg border border-line bg-raised">
        {METRICS.map((metric, index) => {
          const value = scores[metric.key];
          const delta =
            comparison &&
            typeof comparison.baseScores[metric.key] === "number" &&
            typeof comparison.scores[metric.key] === "number"
              ? (comparison.baseScores[metric.key] as number) - (comparison.scores[metric.key] as number)
              : null;
          return (
            <li key={metric.key} className="border-b border-line px-4 py-3.5 last:border-b-0">
              <div className="flex items-baseline gap-3">
                <h3 className="text-sm font-medium">{metric.label}</h3>
                {delta !== null && (
                  <span
                    className={`tnum font-mono text-xs ${
                      delta > 0.005 ? "text-good" : delta < -0.005 ? "text-bad" : "text-muted"
                    }`}
                    title={`Against ${comparison!.name}, over the ${comparison!.shared} questions both runs answered from the corpus`}
                  >
                    {formatDelta(delta)}
                  </span>
                )}
                <span className="tnum ml-auto font-mono text-lg font-medium">{formatScore(value)}</span>
              </div>
              <div
                className="mt-2 h-1.5 overflow-hidden rounded-full bg-sunken"
                role="img"
                aria-label={`${metric.label}: ${formatScore(value)} out of 1`}
              >
                {typeof value === "number" && (
                  <div
                    className="h-full origin-left animate-grow rounded-full bg-accent"
                    style={{ width: `${value * 100}%`, animationDelay: `${index * 70}ms` }}
                  />
                )}
              </div>
              <p className="mt-2 max-w-[68ch] text-xs leading-relaxed text-ink-2">{metric.about}</p>
            </li>
          );
        })}
      </ul>

      {comparison && (
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Differences are {run.name} minus {comparison.name}, computed only over the{" "}
          {comparison.shared} question{comparison.shared === 1 ? "" : "s"} both runs answered from the
          corpus. A web-answered question says nothing about the retrieval settings under test.
        </p>
      )}
    </div>
  );
}

export function SourceSplit({ counts, total }: { counts: Record<AnswerSource, number>; total: number }) {
  const present = SOURCES.filter((source) => counts[source.key] > 0);
  return (
    <div>
      <h3 className="text-xs font-semibold">Where answers came from</h3>
      <div
        className="mt-3 flex h-3 gap-0.5"
        role="img"
        aria-label={present.map((source) => `${source.label}: ${counts[source.key]}`).join(", ")}
      >
        {present.map((source) => (
          <div
            key={source.key}
            title={`${source.label}: ${counts[source.key]} of ${total}`}
            className={`${source.swatch} h-full min-w-1 first:rounded-l-sm last:rounded-r-sm`}
            style={{ flexGrow: counts[source.key] }}
          />
        ))}
      </div>
      <ul className="mt-3 space-y-1.5">
        {SOURCES.map((source) => (
          <li key={source.key} className="flex items-center gap-2 text-xs">
            <span className={`size-2 rounded-xs ${source.swatch}`} />
            <span className="text-ink-2">{source.label}</span>
            <span className="tnum ml-auto font-mono text-ink">{counts[source.key]}</span>
            <span className="tnum w-10 text-right font-mono text-muted">
              {total > 0 ? Math.round((counts[source.key] / total) * 100) : 0}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RunFacts({ run }: { run: RunDetail }) {
  const usage = run.judge_usage;
  const effective = run.config?.effective ?? {};
  const facts: [string, string][] = [
    ["Judge model", run.judge_model ?? "not scored yet"],
    ["Preset", run.config?.preset ?? "full pipeline"],
  ];
  if (typeof effective.FUSION_DENSE_WEIGHT === "number") {
    const dense = effective.FUSION_DENSE_WEIGHT;
    facts.push(["Fusion weights", `${dense.toFixed(2)} dense, ${(1 - dense).toFixed(2)} sparse`]);
  }
  if (usage) {
    facts.push(
      ["Judge tokens", number.format(usage.prompt_tokens + usage.completion_tokens)],
      ["Scoring time", formatDuration(usage.scoring_seconds)],
      ["Rate-limit retries", String(usage.http_429)],
    );
  }

  return (
    <dl>
      {facts.map(([term, detail]) => (
        <div key={term} className="flex items-baseline justify-between gap-4 border-t border-line py-2 text-xs first:border-t-0 first:pt-0">
          <dt className="text-muted">{term}</dt>
          <dd className="tnum truncate text-right font-mono text-ink" title={detail}>
            {detail}
          </dd>
        </div>
      ))}
    </dl>
  );
}
