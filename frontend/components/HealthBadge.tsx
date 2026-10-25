"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL, checkHealth, type HealthResponse } from "@/lib/api";

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
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      let ok = false;
      try {
        const data = await checkHealth();
        ok = true;
        if (active) setState({ kind: "ok", data });
      } catch (error) {
        if (active) {
          setState({
            kind: "down",
            message: error instanceof Error ? error.message : "unreachable",
          });
        }
      }
      // Poll faster while the backend is down, so the badge flips soon after it starts.
      if (active) timer = setTimeout(poll, ok ? 15_000 : 4_000);
    }

    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);

  const label =
    state.kind === "loading"
      ? "Connecting"
      : state.kind === "ok"
        ? `Backend v${state.data.version}`
        : "Backend offline";

  return (
    <span
      role="status"
      className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-line px-2 py-1 text-xs text-ink-2 sm:px-2.5"
      title={state.kind === "down" ? `${API_BASE_URL}: ${state.message}` : API_BASE_URL}
    >
      <span className={`size-1.5 rounded-full ${DOT[state.kind]} ${state.kind === "ok" ? "" : "animate-pulse"}`} />
      <span className="tnum sr-only sm:not-sr-only">{label}</span>
    </span>
  );
}
