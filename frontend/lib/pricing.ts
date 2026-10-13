import type { UsageEvent } from "@/lib/api";

// USD per million tokens, paid-tier Standard prices from ai.google.dev/gemini-api/docs/pricing (updated 2026-10-01).
const PRICES: Record<string, { input: number; output: number }> = {
  "gemini-3.5-flash": { input: 1.5, output: 9.0 },
  "gemini-3.5-flash-lite": { input: 0.3, output: 2.5 },
};

// Tavily bills API credits per search request: docs.tavily.com/documentation/api-credits.
// Pay-as-you-go rate; the first 1,000 credits each month are free, and monthly plans run $0.005-$0.0075.
const TAVILY_USD_PER_CREDIT = 0.008;
const TAVILY_CREDITS: Record<string, number> = {
  "tavily-basic": 1,
  "tavily-fast": 1,
  "tavily-ultra-fast": 1,
  "tavily-advanced": 2,
};

/** Estimated USD cost of one model call or web search, or null when it is unpriced. */
export function callCost(event: UsageEvent): number | null {
  const credits = TAVILY_CREDITS[event.model];
  if (credits !== undefined) return credits * TAVILY_USD_PER_CREDIT;
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
