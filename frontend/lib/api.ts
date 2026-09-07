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

/**
 * The current session id, asking the backend for one on first use.
 *
 * Ids are issued server-side and never invented here: with no authentication,
 * the id is the only thing protecting a session's uploads, so a guessable one
 * would let anyone write into or delete someone else's session.
 */
export async function getSessionId(): Promise<string> {
  const stored = sessionStorage.getItem(SESSION_KEY);
  if (stored) return stored;

  const response = await fetch(apiUrl("/session"), { method: "POST" });
  if (!response.ok) {
    throw new Error(`Could not start a session: ${response.status}`);
  }

  const { session_id: sessionId } = (await response.json()) as {
    session_id: string;
  };
  sessionStorage.setItem(SESSION_KEY, sessionId);
  return sessionId;
}

export function currentSessionId(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

/** Delete this session's uploads and forget the id. */
export async function deleteSession(): Promise<boolean> {
  const sessionId = currentSessionId();
  if (!sessionId) return false;

  const response = await fetch(apiUrl(`/session/${sessionId}`), {
    method: "DELETE",
  });
  sessionStorage.removeItem(SESSION_KEY);
  if (!response.ok) {
    throw new Error(`Could not clear the session: ${response.status}`);
  }
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

/**
 * Upload files to the ingestion endpoint.
 *
 * XMLHttpRequest rather than fetch, because fetch cannot report upload
 * progress. The upload is quick; extraction and embedding then run for minutes
 * with nothing on the wire, and without that distinction the page looks frozen.
 */
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
        resolve(payload as IngestResponse);
        return;
      }

      // FastAPI puts the readable message in `detail`.
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? String((payload as { detail: unknown }).detail)
          : `${request.status} ${request.statusText || "request failed"}`;
      reject(new Error(detail));
    });

    request.addEventListener("error", () => {
      reject(new Error(`Cannot reach the backend at ${API_BASE_URL}`));
    });
    request.addEventListener("abort", () => {
      reject(new Error("Upload cancelled"));
    });

    request.send(body);
  });
}
