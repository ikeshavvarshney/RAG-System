"use client";

import { useEffect, useState } from "react";
import { checkHealth, type HealthResponse } from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "down"; message: string };

const DOT = {
  loading: "bg-warn",
  ok: "bg-good",
  down: "bg-bad",
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
      ? "Checking backend"
      : state.kind === "ok"
        ? `Backend online, v${state.data.version}`
        : "Backend offline";

  return (
    <span
      className="inline-flex items-center gap-2 text-xs text-ink-2"
      title={state.kind === "down" ? state.message : undefined}
    >
      <span className={`size-1.5 rounded-full ${DOT[state.kind]} ${state.kind === "loading" ? "animate-pulse" : ""}`} />
      {label}
    </span>
  );
}
