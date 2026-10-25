import { apiFetch, apiUrl, currentSessionId, errorMessage } from "@/lib/api";

export interface ViewerTarget {
  sourceDoc: string;
  page?: number | null;
  chunkId?: string;
}

export interface Passage {
  chunk_id: string;
  text: string;
  page: number | null;
  location: string | null;
  chunk_type: "text" | "table" | "chart" | "image_caption";
  extraction_method: "vision" | "ocr" | "text";
}

export interface ViewerDocument {
  source_doc: string;
  kind: "pdf" | "image" | "docx" | "unavailable";
  page_count: number;
  passages: Passage[];
}

export interface CorpusDocument {
  source_doc: string;
  chunk_count: number;
  pages: number | null;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

export async function fetchViewerDocument(sourceDoc: string): Promise<ViewerDocument> {
  let response: Response;
  try {
    response = await apiFetch(
      `/viewer/document?${query({ source_doc: sourceDoc, session_id: currentSessionId() })}`,
      { cache: "no-store" },
    );
  } catch {
    throw new Error("Cannot reach the backend.");
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(errorMessage(payload) ?? `Could not open the document: ${response.status}`);
  }
  return payload as ViewerDocument;
}

export function pageImageUrl(sourceDoc: string, page: number, chunkId?: string): string {
  return apiUrl(
    `/viewer/page?${query({ source_doc: sourceDoc, page, chunk_id: chunkId, session_id: currentSessionId() })}`,
  );
}

export async function listCorpusDocuments(): Promise<CorpusDocument[]> {
  const response = await apiFetch("/documents", { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not list the corpus: ${response.status}`);
  return ((await response.json()) as { documents: CorpusDocument[] }).documents;
}

/** Remove one document from the shared corpus. */
export async function deleteCorpusDocument(sourceDoc: string): Promise<void> {
  const response = await apiFetch(`/documents/${encodeURIComponent(sourceDoc)}`, { method: "DELETE" });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new Error(errorMessage(payload) ?? `Could not delete ${sourceDoc}: ${response.status}`);
  }
}

export const PASSAGE_LABELS: Record<Passage["chunk_type"], string> = {
  text: "Text",
  table: "Table",
  chart: "Chart",
  image_caption: "Image",
};

export const METHOD_LABELS: Record<Passage["extraction_method"], string> = {
  text: "read from the text layer",
  vision: "transcribed by the vision model",
  ocr: "recognised by OCR",
};
