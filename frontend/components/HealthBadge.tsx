"use client";

import { useEffect, useState } from "react";
import { checkHealth, type HealthResponse } from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "down"; message: string };

const DOT = {
  loading: "bg-amber-400",
  ok: "bg-emerald-500",
  down: "bg-red-500",
} as const;

export default function HealthBadge() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;

    async function poll() {
      try {
        const data = await checkHealth();
        if (active) setState({ kind: "ok", data });
      } catch (error) {
        if (active) {
          setState({
            kind: "down",
            message: error instanceof Error ? error.message : "unreachable",
          });
        }
      }
    }

    poll();
    const timer = setInterval(poll, 15_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const label =
    state.kind === "loading"
      ? "checking backend..."
      : state.kind === "ok"
        ? `backend ${state.data.status} - v${state.data.version}`
        : "backend offline";

  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-600"
      title={state.kind === "down" ? state.message : undefined}
    >
      <span className={`size-2 rounded-full ${DOT[state.kind]}`} />
      {label}
    </span>
  );
}
