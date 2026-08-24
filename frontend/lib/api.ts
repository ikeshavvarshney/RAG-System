export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
  version: string;
}

/**
 * Join base + /api + path, collapsing duplicate slashes.
 * apiUrl("/health") -> "http://localhost:8000/api/health"
 */
export function apiUrl(path: string): string {
  const base = API_BASE_URL.replace(/\/+$/, "");
  const suffix = `/${path}`.replace(/\/{2,}/g, "/");
  return `${base}/api${suffix}`;
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await fetch(apiUrl("/health"), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(
      `Health check failed: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as HealthResponse;
}