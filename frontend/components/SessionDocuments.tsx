"use client";

import { useEffect, useState } from "react";
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
    return <p className="mt-3 text-sm text-red-600">{error}</p>;
  }

  if (documents.length === 0) {
    return null;
  }

  return (
    <div className="mt-4">
      <h3 className="text-xs font-medium text-neutral-500">
        In this session ({documents.length})
      </h3>

      <ul className="mt-2 space-y-1">
        {documents.map((document) => (
          <li
            key={document.source_doc}
            className="flex items-center gap-3 text-sm"
          >
            <span className="truncate text-neutral-700">
              {document.source_doc}
            </span>
            <span className="shrink-0 text-xs text-neutral-400">
              {document.chunk_count} passage
              {document.chunk_count === 1 ? "" : "s"}
              {document.pages !== null && `, ${document.pages}p`}
            </span>
            <button
              type="button"
              onClick={() => handleRemove(document.source_doc)}
              disabled={removing !== null}
              aria-label={`Remove ${document.source_doc}`}
              className="ml-auto shrink-0 text-xs text-neutral-400 hover:text-neutral-900 disabled:opacity-40"
            >
              {removing === document.source_doc ? "removing..." : "remove"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
