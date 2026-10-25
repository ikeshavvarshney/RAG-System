"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Chevron, Close, Doc } from "@/components/Icons";
import {
  METHOD_LABELS,
  PASSAGE_LABELS,
  fetchViewerDocument,
  pageImageUrl,
  type Passage,
  type ViewerDocument,
  type ViewerTarget,
} from "@/lib/viewer";

export const ViewerContext = createContext<(target: ViewerTarget) => void>(() => undefined);

export const ActiveViewerContext = createContext<ViewerTarget | null>(null);

export function useOpenViewer() {
  return useContext(ViewerContext);
}

export function useActiveViewer() {
  return useContext(ActiveViewerContext);
}

type Tab = "page" | "text";

function PassageBlock({ passage, cited }: { passage: Passage; cited: boolean }) {
  const ref = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (cited) ref.current?.scrollIntoView({ block: "nearest" });
  }, [cited]);

  const structured = passage.chunk_type === "table";
  return (
    <li
      ref={ref}
      className={`rounded-md border px-3 py-2.5 ${
        cited ? "border-accent-line bg-accent-soft" : "border-line bg-raised"
      }`}
    >
      <p className="mb-1.5 text-[11px] text-muted">
        <span className="font-medium text-ink-2">{PASSAGE_LABELS[passage.chunk_type]}</span>,{" "}
        {METHOD_LABELS[passage.extraction_method]}
        {passage.location && `, under "${passage.location}"`}
        {cited && <span className="ml-1.5 font-medium text-accent-strong">Cited passage</span>}
      </p>
      {structured ? (
        <pre className="overflow-x-auto font-mono text-[11px] leading-relaxed text-ink">{passage.text}</pre>
      ) : (
        <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">{passage.text}</p>
      )}
    </li>
  );
}

function PageImage({ src, alt }: { src: string; alt: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  if (state === "failed") {
    return (
      <p role="alert" className="px-4 py-10 text-center text-sm text-ink-2">
        This page could not be rendered. The extracted text is still available.
      </p>
    );
  }
  return (
    <div className="relative min-h-40">
      {state === "loading" && (
        <div className="skeleton absolute inset-x-0 top-0 mx-auto aspect-[1/1.3] w-full max-w-3xl" role="status" aria-label="Rendering page" />
      )}
      {/* eslint-disable-next-line @next/next/no-img-element -- rendered by the backend per request */}
      <img
        src={src}
        alt={alt}
        onLoad={() => setState("ready")}
        onError={() => setState("failed")}
        className={`mx-auto block w-full max-w-3xl rounded-sm border border-line bg-white shadow-[0_2px_10px_rgb(16_19_24/0.08)] transition-opacity duration-200 ${
          state === "ready" ? "opacity-100" : "opacity-0"
        }`}
      />
    </div>
  );
}

export default function DocumentViewer({ target, onClose }: { target: ViewerTarget; onClose: () => void }) {
  const [doc, setDoc] = useState<ViewerDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(target.page ?? 1);
  const [tab, setTab] = useState<Tab>("page");
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let active = true;
    fetchViewerDocument(target.sourceDoc)
      .then((fetched) => {
        if (!active) return;
        setDoc(fetched);
        setTab(fetched.page_count > 0 ? "page" : "text");
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      active = false;
    };
  }, [target.sourceDoc]);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pageCount = doc?.page_count ?? 0;
  const current = Math.min(Math.max(page, 1), Math.max(pageCount, 1));
  const paged = pageCount > 1;

  const passages = useMemo(() => {
    if (!doc) return [];
    if (!paged) return doc.passages;
    return doc.passages.filter((passage) => passage.page === current);
  }, [doc, paged, current]);

  const citedHere = target.chunkId && (target.page ?? 1) === current ? target.chunkId : undefined;
  const tabClass = (value: Tab) =>
    `rounded-[5px] px-2.5 py-0.5 text-xs transition-colors ${
      tab === value
        ? "bg-raised font-medium text-ink shadow-[0_1px_2px_rgb(16_19_24/0.08)]"
        : "text-muted hover:text-ink"
    }`;

  return (
    <aside
      aria-label={`Document viewer: ${target.sourceDoc}`}
      className="fixed inset-0 top-12 z-30 flex flex-col bg-sunken xl:static xl:z-auto xl:w-[44%] xl:min-w-[26rem] xl:max-w-3xl xl:shrink-0 xl:border-l xl:border-line"
    >
      <header className="shrink-0 border-b border-line bg-raised px-4 py-2">
        <div className="flex items-center gap-2">
          <Doc className="shrink-0 text-series-corpus" width="15" height="15" />
          <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold" title={target.sourceDoc}>
            {target.sourceDoc}
          </h2>
          {doc && (
            <span className="tnum hidden shrink-0 text-xs text-muted sm:inline">
              {doc.passages.length} passage{doc.passages.length === 1 ? "" : "s"}
            </span>
          )}
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close document viewer"
            className="shrink-0 rounded p-1.5 text-ink-2 hover:bg-sunken hover:text-ink"
          >
            <Close />
          </button>
        </div>

        {doc && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
            {pageCount > 0 && (
              <div className="flex gap-0.5 rounded-md border border-line bg-sunken p-0.5" role="tablist">
                <button type="button" role="tab" aria-selected={tab === "page"} onClick={() => setTab("page")} className={tabClass("page")}>
                  Page
                </button>
                <button type="button" role="tab" aria-selected={tab === "text"} onClick={() => setTab("text")} className={tabClass("text")}>
                  Extracted text
                </button>
              </div>
            )}

            {paged && (
              <div className="ml-auto flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPage(current - 1)}
                  disabled={current <= 1}
                  aria-label="Previous page"
                  className="rounded p-1 text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-30"
                >
                  <Chevron className="rotate-180" />
                </button>
                <span className="tnum min-w-20 text-center font-mono text-xs text-ink-2">
                  {current} of {pageCount}
                </span>
                <button
                  type="button"
                  onClick={() => setPage(current + 1)}
                  disabled={current >= pageCount}
                  aria-label="Next page"
                  className="rounded p-1 text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-30"
                >
                  <Chevron />
                </button>
              </div>
            )}
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {error && (
          <p role="alert" className="rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-sm text-bad">
            {error}
          </p>
        )}
        {!error && !doc && (
          <div className="mx-auto max-w-3xl" role="status" aria-label="Opening document">
            <div className="skeleton aspect-[1/1.3] w-full" />
          </div>
        )}

        {doc && tab === "page" && pageCount > 0 && (
          <PageImage
            key={`${current}-${citedHere ?? ""}`}
            src={pageImageUrl(doc.source_doc, current, citedHere)}
            alt={`${doc.source_doc}, page ${current}`}
          />
        )}

        {doc && (tab === "text" || pageCount === 0) && (
          <>
            {doc.kind === "docx" && (
              <p className="mb-3 text-xs leading-relaxed text-muted">
                A DOCX file has no fixed pages, so this shows the text as it was extracted and indexed.
              </p>
            )}
            {doc.kind === "unavailable" && (
              <p className="mb-3 text-xs leading-relaxed text-muted">
                The original file is not stored on the server, so only the indexed text can be shown.
                Upload it again to see its pages.
              </p>
            )}
            {passages.length === 0 ? (
              <p className="text-sm text-ink-2">
                Nothing was indexed from {paged ? "this page" : "this document"}.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {passages.map((passage) => (
                  <PassageBlock
                    key={passage.chunk_id}
                    passage={passage}
                    cited={passage.chunk_id === target.chunkId}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
