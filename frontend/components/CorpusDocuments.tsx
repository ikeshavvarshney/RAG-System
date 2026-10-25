"use client";

import { useState } from "react";
import { useActiveViewer, useOpenViewer } from "@/components/DocumentViewer";
import { Search, Trash } from "@/components/Icons";
import { deleteCorpusDocument, type CorpusDocument } from "@/lib/viewer";

interface Props {
  documents: CorpusDocument[] | null;
  offline: boolean;
  onChanged: () => void;
}

export default function CorpusDocuments({ documents, offline, onChanged }: Props) {
  const [filter, setFilter] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const openViewer = useOpenViewer();
  const activeDoc = useActiveViewer()?.sourceDoc;

  async function handleDelete(sourceDoc: string) {
    setDeleting(sourceDoc);
    setError(null);
    try {
      await deleteCorpusDocument(sourceDoc);
      setConfirming(null);
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDeleting(null);
    }
  }

  const needle = filter.trim().toLowerCase();
  const visible = (documents ?? []).filter(
    (document) => !needle || document.source_doc.toLowerCase().includes(needle),
  );
  const passages = (documents ?? []).reduce((sum, document) => sum + document.chunk_count, 0);

  return (
    <section className="mt-5" aria-label="Corpus documents">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-ink">
          Corpus {documents && <span className="tnum font-normal text-muted">{documents.length}</span>}
        </h3>
        {documents && documents.length > 0 && (
          <span className="tnum text-[11px] text-muted">{passages.toLocaleString("en-US")} passages</span>
        )}
      </div>

      {documents === null && offline && (
        <p className="mt-2 flex gap-2 text-xs leading-relaxed text-ink-2" role="status">
          <span className="mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full bg-warn" />
          Waiting for the backend. The list loads as soon as it responds.
        </p>
      )}

      {documents === null && !offline && (
        <div className="mt-2 space-y-2" aria-hidden="true">
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="space-y-1 px-1.5 py-1">
              <div className="skeleton h-3 w-4/5" />
              <div className="skeleton h-2.5 w-1/3" />
            </div>
          ))}
        </div>
      )}

      {documents !== null && documents.length === 0 && (
        <p className="mt-2 rounded-md border border-dashed border-line-strong px-3 py-3 text-xs leading-relaxed text-ink-2">
          The corpus is empty. Upload documents above to make them searchable.
        </p>
      )}

      {documents !== null && documents.length > 6 && (
        <label className="relative mt-2 block">
          <Search className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted" width="14" height="14" />
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter documents"
            aria-label="Filter corpus documents by name"
            className="w-full rounded-md border border-line-strong bg-raised py-1 pl-7 pr-2 text-[13px] outline-none placeholder:text-muted hover:border-ink-2 focus:border-accent"
          />
        </label>
      )}

      {visible.length > 0 && (
        <ul className="mt-1.5 space-y-px">
          {visible.map((document) => {
            const asking = confirming === document.source_doc;
            const busy = deleting === document.source_doc;
            return (
              <li
                key={document.source_doc}
                className={`group rounded ${activeDoc === document.source_doc ? "bg-accent-soft" : "hover:bg-raised"}`}
              >
                <div className="flex items-center gap-1">
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
                  {!asking && (
                    <button
                      type="button"
                      onClick={() => setConfirming(document.source_doc)}
                      aria-label={`Delete ${document.source_doc} from the corpus`}
                      title="Delete from corpus"
                      className="mr-1 shrink-0 rounded p-1 text-muted opacity-0 hover:bg-bad-soft hover:text-bad focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Trash width="14" height="14" />
                    </button>
                  )}
                </div>
                {asking && (
                  <div className="flex items-center gap-2 px-1.5 pb-1.5 text-xs">
                    <span className="text-ink-2">Delete for everyone?</span>
                    <button
                      type="button"
                      onClick={() => handleDelete(document.source_doc)}
                      disabled={busy}
                      className="ml-auto rounded bg-bad px-2 py-0.5 font-medium text-white hover:bg-bad/85 disabled:opacity-50"
                    >
                      {busy ? "Deleting" : "Delete"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirming(null)}
                      disabled={busy}
                      className="rounded px-2 py-0.5 text-ink-2 hover:bg-sunken hover:text-ink"
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {documents !== null && documents.length > 0 && visible.length === 0 && (
        <p className="mt-2 px-1.5 text-xs text-muted">No document matches “{filter}”.</p>
      )}

      {error && (
        <p role="alert" className="mt-2 text-xs text-bad">
          {error}
        </p>
      )}
    </section>
  );
}
