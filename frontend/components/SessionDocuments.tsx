"use client";

import { useState } from "react";
import { useActiveViewer, useOpenViewer } from "@/components/DocumentViewer";
import { Close } from "@/components/Icons";
import { deleteSessionDocument, type SessionDocument } from "@/lib/api";

interface Props {
  documents: SessionDocument[];
  onChanged: () => void;
}

export default function SessionDocuments({ documents, onChanged }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const openViewer = useOpenViewer();
  const activeDoc = useActiveViewer()?.sourceDoc;

  async function run(key: string, action: () => Promise<unknown>) {
    setRemoving(key);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving(null);
    }
  }

  if (documents.length === 0) return null;

  return (
    <section className="mt-5 rounded-md border border-accent-line bg-accent-soft/60 p-2.5" aria-label="Chat documents">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-ink">
          This chat <span className="tnum font-normal text-muted">{documents.length}</span>
        </h3>
        <button
          type="button"
          onClick={() =>
            run("*", async () => {
              for (const document of documents) await deleteSessionDocument(document.source_doc);
            })
          }
          disabled={removing !== null}
          className="text-xs text-accent-strong hover:underline disabled:opacity-40"
        >
          {removing === "*" ? "Clearing" : "Clear all"}
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
        Questions are answered from these documents only, not the corpus.
      </p>

      <ul className="mt-2 space-y-px">
        {documents.map((document) => (
          <li
            key={document.source_doc}
            className={`group flex items-center gap-1 rounded ${activeDoc === document.source_doc ? "bg-raised" : "hover:bg-raised"}`}
          >
            <button
              type="button"
              onClick={() => openViewer({ sourceDoc: document.source_doc })}
              title={`Open ${document.source_doc}`}
              className="min-w-0 flex-1 px-1.5 py-1 text-left"
            >
              <span className={`block truncate text-[13px] ${activeDoc === document.source_doc ? "font-medium text-accent-strong" : "text-ink"}`}>
                {document.source_doc}
              </span>
              <span className="tnum block text-[11px] text-muted">
                {document.chunk_count} passage{document.chunk_count === 1 ? "" : "s"}
                {document.pages !== null && ` · ${document.pages} pages`}
              </span>
            </button>
            <button
              type="button"
              onClick={() => run(document.source_doc, () => deleteSessionDocument(document.source_doc))}
              disabled={removing !== null}
              aria-label={`Remove ${document.source_doc} from this chat`}
              title="Remove from chat"
              className="mr-1 shrink-0 rounded p-1 text-muted opacity-0 hover:bg-sunken hover:text-ink focus-visible:opacity-100 disabled:opacity-40 group-hover:opacity-100"
            >
              {removing === document.source_doc ? (
                <span className="block size-3.5 animate-pulse rounded-full bg-line-strong" />
              ) : (
                <Close width="14" height="14" />
              )}
            </button>
          </li>
        ))}
      </ul>

      {error && (
        <p role="alert" className="mt-2 text-xs text-bad">
          {error}
        </p>
      )}
    </section>
  );
}
