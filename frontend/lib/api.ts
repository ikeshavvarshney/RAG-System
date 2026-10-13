import { log, logRequest } from "@/lib/logger";

export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
  version: string;
}

/** Join base + /api + path, collapsing duplicate slashes. */
export function apiUrl(path: string): string {
  const base = API_BASE_URL.replace(/\/+$/, "");
  const suffix = `/${path}`.replace(/\/{2,}/g, "/");
  return `${base}/api${suffix}`;
}

/** The readable message from the backend's `{"error": {"message"}}` body. */
export function errorMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const error = (payload as { error?: { message?: unknown } }).error;
  return typeof error?.message === "string" ? error.message : null;
}

/** fetch() against the API, logging method, path, status, duration and the backend's request id. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const startedAt = performance.now();
  try {
    const response = await fetch(apiUrl(path), init);
    logRequest(method, path, response.status, startedAt, response.headers.get("x-request-id"));
    return response;
  } catch (error) {
    if (!init.signal?.aborted) {
      logRequest(method, path, 0, startedAt, null, { error: String(error) });
    }
    throw error;
  }
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await apiFetch("/health", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(
      `Health check failed: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as HealthResponse;
}

/* -------------------------------------------------------------------------- */
/* Ingestion                                                                  */
/* -------------------------------------------------------------------------- */

// Mirrors the endpoint's limits, so a doomed batch fails instantly instead of
// after a long upload.
export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
export const MAX_FILES_PER_REQUEST = 60;
export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;

export interface IngestFailure {
  filename: string;
  reason: string;
}

export interface IngestResponse {
  chunk_count: number;
  indexed: {
    total: number;
    by_extraction_method: Record<string, number>;
    failed: number;
    failure_reason: string | null;
    vector_store_total: number;
    keyword_index_total: number;
  };
  succeeded: string[];
  failed: IngestFailure[];
  corpus_scope: string;
}

/** Where an upload should land. */
export type IngestTarget = "corpus" | "session";

const SESSION_KEY = "rag.session_id";

// localStorage, not sessionStorage: sessionStorage is cleared when the tab closes, which loses the
// id while the server still holds the uploads.
function readStoredId(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    // Private mode, or site data blocked.
    return null;
  }
}

function writeStoredId(sessionId: string | null): void {
  try {
    if (sessionId === null) localStorage.removeItem(SESSION_KEY);
    else localStorage.setItem(SESSION_KEY, sessionId);
  } catch {
    // A session that cannot be remembered still works for this page view.
  }
}

/** The current session id, asking the backend for one on first use. */
export async function getSessionId(): Promise<string> {
  const stored = readStoredId();
  if (stored) return stored;

  const response = await apiFetch("/session", { method: "POST" });
  if (!response.ok) {
    throw new Error(`Could not start a session: ${response.status}`);
  }

  const { session_id: sessionId } = (await response.json()) as {
    session_id: string;
  };
  writeStoredId(sessionId);
  return sessionId;
}

export function currentSessionId(): string | null {
  return readStoredId();
}

export interface SessionDocument {
  source_doc: string;
  chunk_count: number;
  pages: number | null;
  extraction_methods: string[];
}

/** What the backend holds for this session, or [] if there is no session yet. */
export async function listSessionDocuments(): Promise<SessionDocument[]> {
  const sessionId = currentSessionId();
  if (!sessionId) return [];

  const response = await apiFetch(`/session/${sessionId}/documents`, {
    cache: "no-store",
  });

  if (response.status === 400) {
    // An id the server will not accept: it expired, or the store was reset.
    // Forget it so the next upload starts a fresh session.
    writeStoredId(null);
    return [];
  }
  if (!response.ok) {
    throw new Error(`Could not list session documents: ${response.status}`);
  }

  return ((await response.json()) as { documents: SessionDocument[] }).documents;
}

/** Remove one uploaded document from this session. */
export async function deleteSessionDocument(sourceDoc: string): Promise<number> {
  const sessionId = currentSessionId();
  if (!sessionId) return 0;

  const response = await apiFetch(
    `/session/${sessionId}/documents/${encodeURIComponent(sourceDoc)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw new Error(`Could not remove ${sourceDoc}: ${response.status}`);
  }
  return ((await response.json()) as { deleted_chunks: number }).deleted_chunks;
}

/** Delete this session's uploads and forget the id. */
export async function deleteSession(): Promise<boolean> {
  const sessionId = currentSessionId();
  if (!sessionId) return false;

  const response = await apiFetch(`/session/${sessionId}`, {
    method: "DELETE",
  });

  // Only forget the id once the server has confirmed.
  if (!response.ok) {
    throw new Error(`Could not clear the session: ${response.status}`);
  }
  writeStoredId(null);
  return ((await response.json()) as { deleted: boolean }).deleted;
}

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Why this file cannot be sent, or null if it can. */
export function rejectionReason(file: File): string | null {
  if (!ACCEPTED_EXTENSIONS.includes(fileExtension(file.name))) {
    return `unsupported type (${ACCEPTED_EXTENSIONS.join(", ")})`;
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return `too large (${formatBytes(file.size)}, max ${formatBytes(MAX_FILE_SIZE_BYTES)})`;
  }
  if (file.size === 0) {
    return "empty file";
  }
  return null;
}

/** Upload files to the ingestion endpoint. */
export function ingestFiles(
  files: File[],
  onUploadProgress?: (fraction: number) => void,
  sessionId?: string,
): Promise<IngestResponse> {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file, file.name);
  }
  // Omitted entirely for a corpus load: the backend reads its absence as
  // "persistent", so sending an empty value would not mean the same thing.
  if (sessionId) {
    body.append("session_id", sessionId);
  }

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", apiUrl("/ingest"));
    const startedAt = performance.now();
    const logDone = (status: number, details?: Record<string, unknown>) =>
      logRequest("POST", "/ingest", status, startedAt, request.getResponseHeader("x-request-id"), {
        files: files.length,
        ...details,
      });

    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onUploadProgress) {
        onUploadProgress(event.loaded / event.total);
      }
    });

    request.addEventListener("load", () => {
      let payload: unknown = null;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        // Falls through to the status-based message below.
      }

      if (request.status >= 200 && request.status < 300) {
        const result = payload as IngestResponse;
        logDone(request.status, {
          succeeded: result.succeeded.length,
          failed: result.failed.length,
          chunks: result.indexed.total,
        });
        resolve(result);
        return;
      }

      logDone(request.status, { error: errorMessage(payload) });
      reject(
        new Error(
          errorMessage(payload) ??
            `${request.status} ${request.statusText || "request failed"}`,
        ),
      );
    });

    request.addEventListener("error", () => {
      logDone(0);
      reject(new Error(`Cannot reach the backend at ${API_BASE_URL}`));
    });
    request.addEventListener("abort", () => {
      log("info", "POST /ingest cancelled");
      reject(new Error("Upload cancelled"));
    });

    request.send(body);
  });
}

/* -------------------------------------------------------------------------- */
/* Query                                                                      */
/* -------------------------------------------------------------------------- */

export interface CorpusCitation {
  kind: "corpus";
  source_doc: string;
  page: number | null;
  chunk_id: string;
  score: number | null;
  snippet: string;
}

export interface WebCitation {
  kind: "web";
  source_url: string;
  title: string;
  score: number | null;
  snippet: string;
}

export type Citation = CorpusCitation | WebCitation;

export interface StageTiming {
  stage: string;
  status: "completed" | "failed";
  duration_ms: number | null;
  sub_question: number | null;
}

export interface StageUsage {
  prompt_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface UsageSummary extends StageUsage {
  by_stage: Record<string, StageUsage>;
}

export interface ClaimVerdict {
  index: number;
  text: string;
  markers: number[];
  supported: boolean;
  reason: string;
}

export interface QueryResponse {
  session_id: string;
  answer: string;
  citations: Citation[];
  stages: StageTiming[];
  cache_hit: boolean;
  decomposed: boolean;
  sub_questions: string[] | null;
  raw_question: string;
  resolved_question: string;
  usage: UsageSummary;
  groundedness: { claims: ClaimVerdict[]; score: number | null } | null;
  safety: {
    verdict: "pass" | "fail";
    reason: string;
    source: "deterministic" | "llm";
  } | null;
  removed_claims: { text: string; reason: string }[];
}

export interface StageEvent {
  stage: string;
  status: "started" | "completed" | "failed";
  duration_ms?: number;
  sub_question?: number;
}

export interface UsageEvent {
  stage: string;
  model: string;
  prompt_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface QueryHandlers {
  onStage?: (event: StageEvent) => void;
  onUsage?: (event: UsageEvent) => void;
}

/** Ask a question over the SSE endpoint. Resolves with the terminal `result`. */
export async function streamQuery(
  question: string,
  handlers: QueryHandlers = {},
  signal?: AbortSignal,
): Promise<QueryResponse> {
  const sessionId = await getSessionId();
  const startedAt = performance.now();
  let response: Response;
  try {
    response = await apiFetch("/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId }),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(`Cannot reach the backend at ${API_BASE_URL}`);
  }

  if (!response.ok || !response.body) {
    const payload: unknown = await response.json().catch(() => null);
    if (response.status === 400) writeStoredId(null);
    throw new Error(errorMessage(payload) ?? `Query failed: ${response.status}`);
  }

  const requestId = response.headers.get("x-request-id");
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");

    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      let name = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length === 0) continue;
      const payload = JSON.parse(data.join("\n"));

      if (name === "stage") handlers.onStage?.(payload as StageEvent);
      else if (name === "usage") handlers.onUsage?.(payload as UsageEvent);
      else if (name === "result") {
        const result = payload as QueryResponse;
        log("info", `query answered in ${Math.round(performance.now() - startedAt)}ms`, {
          requestId,
          cacheHit: result.cache_hit,
          citations: result.citations.length,
          groundedness: result.groundedness?.score ?? null,
          tokens: result.usage.total_tokens,
        });
        return result;
      } else if (name === "error") {
        log("error", "query stream sent an error event", { requestId, ...payload });
        throw new Error(errorMessage({ error: payload }) ?? "The query failed.");
      }
    }
  }
  log("error", "query stream closed before a result", { requestId });
  throw new Error("The stream closed before an answer arrived.");
}
