"use client";

import { useEffect, useState } from "react";
import { useOpenViewer } from "@/components/DocumentViewer";
import {
  deleteSessionDocument,
  listSessionDocuments,
  type SessionDocument,
} from "@/lib/api";

interface Props {
  /** Bumped by the parent after an ingest, to trigger a refetch. */
  refreshToken: number;
  onChanged?: () => void;
}

export default function SessionDocuments({ refreshToken, onChanged }: Props) {
  const [documents, setDocuments] = useState<SessionDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const openViewer = useOpenViewer();

  // Fetched on mount and after each mutation, never polled: this client is the
  // only writer to its own session, so there is nothing to discover on a timer.
  useEffect(() => {
    let active = true;

    listSessionDocuments()
      .then((fetched) => {
        // A response that arrived after a newer request was issued would
        // otherwise overwrite fresher data.
        if (!active) return;
        setDocuments(fetched);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      });

    return () => {
      active = false;
    };
  }, [refreshToken, reloads]);

  async function handleRemove(sourceDoc: string) {
    setRemoving(sourceDoc);
    try {
      await deleteSessionDocument(sourceDoc);
      setReloads((count) => count + 1);
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving(null);
    }
  }

  if (error) {
    return (
      <p role="alert" className="mt-3 text-xs text-bad">
        {error}
      </p>
    );
  }

  if (documents.length === 0) {
    return (
      <p className="mt-4 text-xs leading-relaxed text-muted">
        Nothing uploaded to this session yet. While it holds documents,
        questions are answered from those alone.
      </p>
    );
  }

  return (
    <div className="mt-4">
      <h3 className="text-xs font-medium text-ink-2">
        In this session ({documents.length})
      </h3>

      <ul className="mt-2 divide-y divide-line border-y border-line">
        {documents.map((document) => (
          <li key={document.source_doc} className="flex items-center gap-2 py-1.5 text-sm">
            <div className="min-w-0">
              <button
                type="button"
                onClick={() => openViewer({ sourceDoc: document.source_doc })}
                title={`Open ${document.source_doc}`}
                className="block max-w-full truncate text-left text-ink underline decoration-line-strong hover:decoration-ink"
              >
                {document.source_doc}
              </button>
              <p className="tnum text-xs text-muted">
                {document.chunk_count} passage
                {document.chunk_count === 1 ? "" : "s"}
                {document.pages !== null && `, ${document.pages} pages`}
              </p>
            </div>
            <button
              type="button"
              onClick={() => handleRemove(document.source_doc)}
              disabled={removing !== null}
              aria-label={`Remove ${document.source_doc}`}
              className="ml-auto shrink-0 text-xs text-ink-2 underline decoration-line-strong hover:text-ink disabled:opacity-40"
            >
              {removing === document.source_doc ? "Removing" : "Remove"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
