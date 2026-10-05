type Level = "debug" | "info" | "warn" | "error";

const ORDER: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

function threshold(): number {
  const configured = (process.env.NEXT_PUBLIC_LOG_LEVEL ?? "info").toLowerCase();
  return ORDER[configured as Level] ?? ORDER.info;
}

export function log(level: Level, message: string, details?: Record<string, unknown>): void {
  if (ORDER[level] < threshold()) return;
  const line = `[api] ${message}`;
  const write = level === "debug" ? console.debug : console[level];
  if (details) write(line, details);
  else write(line);
}

export function levelForStatus(status: number): Level {
  if (status >= 500 || status === 0) return "error";
  if (status >= 400) return "warn";
  return "info";
}

/** One line per finished request, in the same shape as the backend's request log. */
export function logRequest(
  method: string,
  path: string,
  status: number,
  startedAt: number,
  requestId?: string | null,
  details?: Record<string, unknown>,
): void {
  const ms = Math.round(performance.now() - startedAt);
  const level = path.endsWith("/health") && status < 400 ? "debug" : levelForStatus(status);
  log(level, `${method} ${path} ${status || "network error"} ${ms}ms${requestId ? ` id=${requestId}` : ""}`, details);
}
