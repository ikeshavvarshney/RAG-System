import type { UsageEvent } from "@/lib/api";
import { callCost, formatCost } from "@/lib/pricing";

interface Row {
  stage: string;
  models: Set<string>;
  prompt: number;
  output: number;
  calls: number;
  cost: number;
  unpriced: boolean;
}

function group(events: UsageEvent[]): Row[] {
  const rows = new Map<string, Row>();
  for (const event of events) {
    const row = rows.get(event.stage) ?? {
      stage: event.stage,
      models: new Set<string>(),
      prompt: 0,
      output: 0,
      calls: 0,
      cost: 0,
      unpriced: false,
    };
    const cost = callCost(event);
    row.models.add(event.model);
    row.prompt += event.prompt_tokens;
    row.output += event.output_tokens;
    row.calls += 1;
    row.cost += cost ?? 0;
    row.unpriced ||= cost === null;
    rows.set(event.stage, row);
  }
  return [...rows.values()];
}

const number = new Intl.NumberFormat("en-US");

export default function UsagePanel({ events }: { events: UsageEvent[] }) {
  if (events.length === 0) return null;
  const rows = group(events);
  const tokens = rows.reduce((sum, row) => sum + row.prompt + row.output, 0);
  const cost = rows.reduce((sum, row) => sum + row.cost, 0);
  const unpriced = rows.some((row) => row.unpriced);

  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-xs text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
        <span className="tnum">{number.format(tokens)}</span> tokens, est.{" "}
        <span className="tnum">
          {formatCost(cost)}
          {unpriced && "+"}
        </span>
      </summary>
      <table className="tnum mt-2 w-full max-w-md text-xs">
        <thead className="text-left text-muted">
          <tr className="border-b border-line">
            <th className="py-1 font-normal">Stage</th>
            <th className="py-1 text-right font-normal">In</th>
            <th className="py-1 text-right font-normal">Out</th>
            <th className="py-1 text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody className="text-ink-2">
          {rows.map((row) => (
            <tr key={row.stage} title={[...row.models].join(", ")}>
              <td className="py-0.5 capitalize">{row.stage.replace(/^query_/, "").replace(/_/g, " ")}</td>
              {row.prompt + row.output === 0 ? (
                <td colSpan={2} className="text-right">
                  {row.calls} {row.calls === 1 ? "request" : "requests"}
                </td>
              ) : (
                <>
                  <td className="text-right font-mono text-[11px]">{number.format(row.prompt)}</td>
                  <td className="text-right font-mono text-[11px]">{number.format(row.output)}</td>
                </>
              )}
              <td className="text-right font-mono text-[11px]">
                {row.unpriced ? "n/a" : formatCost(row.cost)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1.5 text-[11px] text-muted">
        Estimated from Gemini list prices and the Tavily pay-as-you-go rate per search.
      </p>
    </details>
  );
}
