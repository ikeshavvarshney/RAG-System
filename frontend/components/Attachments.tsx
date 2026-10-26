"use client";

import { useEffect, useRef, useState } from "react";
import { Close, Doc, Plus } from "@/components/Icons";
import {
  ACCEPTED_EXTENSIONS,
  MAX_FILES_PER_REQUEST,
  getSessionId,
  ingestFiles,
  rejectionReason,
  type IngestResponse,
  type IngestTarget,
} from "@/lib/api";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; files: string[]; fraction: number }
  | { kind: "processing"; files: string[] }
  | { kind: "done"; result: IngestResponse; target: IngestTarget }
  | { kind: "error"; message: string };

const OPTIONS: { target: IngestTarget; label: string; hint: string }[] = [
  { target: "session", label: "Add files to this chat", hint: "Answers come from these files only" },
  { target: "corpus", label: "Add files to the corpus", hint: "Stored permanently, searched by everyone" },
];

export function useAttachments(
  onIngested: (result: IngestResponse, target: IngestTarget, sessionId: string | undefined) => void,
) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const busy = phase.kind === "uploading" || phase.kind === "processing";

  async function upload(incoming: FileList | File[], target: IngestTarget) {
    if (busy) return;
    const accepted: File[] = [];
    const refused: { name: string; reason: string }[] = [];
    for (const file of Array.from(incoming)) {
      const reason = rejectionReason(file);
      if (reason) refused.push({ name: file.name, reason });
      else accepted.push(file);
    }
    if (accepted.length > MAX_FILES_PER_REQUEST) {
      refused.push({
        name: `${accepted.length - MAX_FILES_PER_REQUEST} more file(s)`,
        reason: `over the ${MAX_FILES_PER_REQUEST} file limit for one upload`,
      });
    }
    const files = accepted.slice(0, MAX_FILES_PER_REQUEST);
    setRejected(refused);
    if (files.length === 0) {
      setPhase({ kind: "idle" });
      return;
    }

    const names = files.map((file) => file.name);
    setPhase({ kind: "uploading", files: names, fraction: 0 });
    try {
      const sessionId = target === "session" ? await getSessionId() : undefined;
      const result = await ingestFiles(
        files,
        (fraction) =>
          setPhase(fraction >= 1 ? { kind: "processing", files: names } : { kind: "uploading", files: names, fraction }),
        sessionId,
      );
      setPhase(target === "session" && result.failed.length === 0 ? { kind: "idle" } : { kind: "done", result, target });
      onIngested(result, target, sessionId);
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  function dismiss() {
    setPhase({ kind: "idle" });
    setRejected([]);
  }

  return { phase, rejected, busy, upload, dismiss };
}

export function AttachButton({ disabled, onPick }: { disabled: boolean; onPick: (files: FileList, target: IngestTarget) => void }) {
  const [open, setOpen] = useState(false);
  const targetRef = useRef<IngestTarget>("session");
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div ref={menuRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={disabled}
        aria-label="Add files"
        title="Add files"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex size-8 items-center justify-center rounded-md text-ink-2 transition-colors hover:bg-sunken hover:text-ink disabled:text-muted disabled:hover:bg-transparent"
      >
        <Plus />
      </button>
      {open && (
        <div role="menu" className="absolute bottom-full left-0 z-10 mb-2 w-64 rounded-lg border border-line bg-raised p-1 shadow-[0_8px_24px_rgb(16_19_24/0.12)]">
          {OPTIONS.map((option) => (
            <button
              key={option.target}
              type="button"
              role="menuitem"
              onClick={() => {
                targetRef.current = option.target;
                setOpen(false);
                inputRef.current?.click();
              }}
              className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-sunken"
            >
              <Doc className="mt-0.5 shrink-0 text-muted" width="15" height="15" />
              <span>
                <span className="block text-[13px] text-ink">{option.label}</span>
                <span className="block text-[11px] text-muted">{option.hint}</span>
              </span>
            </button>
          ))}
          <p className="px-2.5 pb-1 pt-1.5 text-[11px] text-muted">PDF, DOCX, JPG, PNG · 50 MB each</p>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS.join(",")}
        onChange={(event) => {
          if (event.target.files?.length) onPick(event.target.files, targetRef.current);
          event.target.value = "";
        }}
        className="hidden"
      />
    </div>
  );
}

export function AttachStatus({
  phase,
  rejected,
  onDismiss,
}: {
  phase: Phase;
  rejected: { name: string; reason: string }[];
  onDismiss: () => void;
}) {
  if (phase.kind === "idle" && rejected.length === 0) return null;

  return (
    <div className="border-b border-line px-2 pb-2 pt-1 text-xs">
      {(phase.kind === "uploading" || phase.kind === "processing") && (
        <div role="status">
          <ul className="flex flex-wrap gap-1.5">
            {phase.files.map((name) => (
              <li key={name} className="flex max-w-56 items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1">
                <Doc className="shrink-0 text-muted" width="13" height="13" />
                <span className="truncate text-ink">{name}</span>
              </li>
            ))}
          </ul>
          <p className="tnum mt-1.5 text-ink-2">
            {phase.kind === "uploading"
              ? `Uploading ${Math.round(phase.fraction * 100)}%`
              : "Extracting and indexing. Each figure costs a vision call, so a large batch takes a few minutes."}
          </p>
          {phase.kind === "uploading" ? (
            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-line">
              <div className="h-full bg-accent transition-[width] duration-150" style={{ width: `${Math.round(phase.fraction * 100)}%` }} />
            </div>
          ) : (
            <div className="skeleton mt-1 h-1 w-full" />
          )}
        </div>
      )}

      {(phase.kind === "done" || phase.kind === "error" || rejected.length > 0) && (
        <div
          role={phase.kind === "error" || rejected.length > 0 ? "alert" : "status"}
          className="flex gap-2 leading-relaxed"
        >
          <div className="min-w-0 flex-1">
            {phase.kind === "error" && <p className="text-bad">{phase.message}</p>}
            {phase.kind === "done" && <DoneSummary result={phase.result} target={phase.target} />}
            {rejected.length > 0 && (
              <ul className="space-y-0.5 text-bad">
                {rejected.map((item) => (
                  <li key={item.name}>
                    {item.name}: {item.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button type="button" onClick={onDismiss} aria-label="Dismiss" className="h-fit shrink-0 rounded p-0.5 text-muted hover:text-ink">
            <Close width="13" height="13" />
          </button>
        </div>
      )}
    </div>
  );
}

function DoneSummary({ result, target }: { result: IngestResponse; target: IngestTarget }) {
  return (
    <>
      {result.succeeded.length > 0 && (
        <p className="text-ink">
          Indexed <span className="tnum font-medium">{result.indexed.total}</span> passage
          {result.indexed.total === 1 ? "" : "s"} from {result.succeeded.length} document
          {result.succeeded.length === 1 ? "" : "s"} into {target === "corpus" ? "the corpus" : "this chat"}.
        </p>
      )}
      {result.failed.length > 0 && (
        <ul className="space-y-0.5 text-bad">
          {result.failed.map((failure) => (
            <li key={failure.filename}>
              {failure.filename}: {failure.reason}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
