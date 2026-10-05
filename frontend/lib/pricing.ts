import type { UsageEvent } from "@/lib/api";

// USD per million tokens, Gemini API list prices (paid tier) as of September 2026.
const PRICES: Record<string, { input: number; output: number }> = {
  "gemini-3.5-flash": { input: 1.5, output: 9.0 },
  "gemini-3.5-flash-lite": { input: 0.3, output: 2.5 },
};

/** Estimated USD cost of one model call, or null for an unpriced model. */
export function callCost(event: UsageEvent): number | null {
  const price = PRICES[event.model];
  if (!price) return null;
  return (
    (event.prompt_tokens * price.input + event.output_tokens * price.output) /
    1_000_000
  );
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `$${usd.toFixed(4)}`;
}
