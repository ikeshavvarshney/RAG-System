import type { UsageEvent } from "@/lib/api";
import { callCost, formatCost } from "@/lib/pricing";

interface Row {
  stage: string;
  models: Set<string>;
  prompt: number;
  output: number;
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
      cost: 0,
      unpriced: false,
    };
    const cost = callCost(event);
    row.models.add(event.model);
    row.prompt += event.prompt_tokens;
    row.output += event.output_tokens;
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
    <details className="mt-3 border-t border-neutral-200 pt-2">
      <summary className="cursor-pointer text-xs text-neutral-500">
        {number.format(tokens)} tokens, est. {formatCost(cost)}
        {unpriced && "+"}
      </summary>
      <table className="mt-2 w-full text-xs tabular-nums">
        <thead className="text-left text-neutral-400">
          <tr>
            <th className="font-normal">Stage</th>
            <th className="text-right font-normal">In</th>
            <th className="text-right font-normal">Out</th>
            <th className="text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody className="text-neutral-600">
          {rows.map((row) => (
            <tr key={row.stage} title={[...row.models].join(", ")}>
              <td>{row.stage.replace(/^query_/, "").replace(/_/g, " ")}</td>
              <td className="text-right">{number.format(row.prompt)}</td>
              <td className="text-right">{number.format(row.output)}</td>
              <td className="text-right">{row.unpriced ? "n/a" : formatCost(row.cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-neutral-400">
        Estimated from Gemini list prices; web search is not included.
      </p>
    </details>
  );
}
