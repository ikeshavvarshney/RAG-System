"use client";

import { useEffect, useState } from "react";
import { useOpenViewer } from "@/components/DocumentViewer";
import { listCorpusDocuments, type CorpusDocument } from "@/lib/viewer";

export default function CorpusDocuments({ refreshToken }: { refreshToken: number }) {
  const [documents, setDocuments] = useState<CorpusDocument[] | null>(null);
  const [filter, setFilter] = useState("");
  const [attempt, setAttempt] = useState(0);
  const openViewer = useOpenViewer();

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    listCorpusDocuments()
      .then((fetched) => {
        if (active) setDocuments(fetched);
      })
      .catch(() => {
        if (active) retry = setTimeout(() => setAttempt((count) => count + 1), 5000);
      });
    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, [refreshToken, attempt]);

  if (!documents || documents.length === 0) return null;

  const needle = filter.trim().toLowerCase();
  const visible = needle
    ? documents.filter((document) => document.source_doc.toLowerCase().includes(needle))
    : documents;

  return (
    <details className="mt-6 border-t border-line pt-4" open>
      <summary className="cursor-pointer text-xs font-medium text-ink-2 hover:text-ink">
        In the corpus ({documents.length})
      </summary>

      {documents.length > 8 && (
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter by name"
          aria-label="Filter corpus documents by name"
          className="mt-3 w-full rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-sm outline-none placeholder:text-muted hover:border-ink focus:border-ink"
        />
      )}

      <ul className="mt-2 divide-y divide-line">
        {visible.map((document) => (
          <li key={document.source_doc}>
            <button
              type="button"
              onClick={() => openViewer({ sourceDoc: document.source_doc })}
              title={`Open ${document.source_doc}`}
              className="group block w-full py-1.5 text-left"
            >
              <span className="block truncate text-sm text-ink group-hover:underline">
                {document.source_doc}
              </span>
              <span className="tnum text-xs text-muted">
                {document.chunk_count} passage{document.chunk_count === 1 ? "" : "s"}
                {document.pages !== null && `, ${document.pages} indexed pages`}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {visible.length === 0 && <p className="mt-2 text-xs text-muted">No document matches that name.</p>}
    </details>
  );
}
