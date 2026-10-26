"use client";

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Close, Upload } from "@/components/Icons";
import {
  ACCEPTED_EXTENSIONS,
  MAX_FILES_PER_REQUEST,
  formatBytes,
  getSessionId,
  ingestFiles,
  rejectionReason,
  type IngestResponse,
  type IngestTarget,
} from "@/lib/api";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; fraction: number }
  | { kind: "processing" }
  | { kind: "done"; result: IngestResponse }
  | { kind: "error"; message: string };

interface Props {
  onIngested?: (result: IngestResponse, target: IngestTarget) => void;
}

const TARGETS: { value: IngestTarget; label: string; hint: string }[] = [
  {
    value: "session",
    label: "This chat",
    hint: "Private to this chat. While the chat holds documents, questions are answered from them alone.",
  },
  {
    value: "corpus",
    label: "Corpus",
    hint: "Stored permanently and searched by everyone using this system.",
  },
];

export default function FileUpload({ onIngested }: Props) {
  const [queue, setQueue] = useState<File[]>([]);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);
  const [target, setTarget] = useState<IngestTarget>("session");
  const inputRef = useRef<HTMLInputElement>(null);

  const busy = phase.kind === "uploading" || phase.kind === "processing";

  function addFiles(incoming: FileList | null) {
    if (!incoming || busy) return;

    const accepted: File[] = [];
    const refused: { name: string; reason: string }[] = [];

    for (const file of Array.from(incoming)) {
      const reason = rejectionReason(file);
      if (reason) {
        refused.push({ name: file.name, reason });
      } else if (![...queue, ...accepted].some((q) => q.name === file.name && q.size === file.size)) {
        accepted.push(file);
      }
    }

    const merged = [...queue, ...accepted];
    if (merged.length > MAX_FILES_PER_REQUEST) {
      refused.push({
        name: `${merged.length - MAX_FILES_PER_REQUEST} more file(s)`,
        reason: `over the ${MAX_FILES_PER_REQUEST} file limit for one upload`,
      });
    }

    setQueue(merged.slice(0, MAX_FILES_PER_REQUEST));
    setRejected(refused);
    setPhase({ kind: "idle" });
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function handlePick(event: ChangeEvent<HTMLInputElement>) {
    addFiles(event.target.files);
    // Clear the input so picking the same file again still fires onChange.
    event.target.value = "";
  }

  function removeAt(index: number) {
    if (busy) return;
    setQueue((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleUpload() {
    if (queue.length === 0 || busy) return;

    setPhase({ kind: "uploading", fraction: 0 });
    try {
      const sessionId = target === "session" ? await getSessionId() : undefined;
      const result = await ingestFiles(
        queue,
        (fraction) => {
          setPhase(fraction >= 1 ? { kind: "processing" } : { kind: "uploading", fraction });
        },
        sessionId,
      );
      setQueue([]);
      setRejected([]);
      setPhase({ kind: "done", result });
      onIngested?.(result, target);
    } catch (error) {
      setPhase({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const activeTarget = TARGETS.find((option) => option.value === target)!;

  return (
    <div>
      <fieldset disabled={busy}>
        <legend className="text-xs font-medium text-ink-2">Upload to</legend>
        <div className="mt-1.5 grid grid-cols-2 gap-0.5 rounded-md border border-line bg-sunken p-0.5">
          {TARGETS.map((option) => (
            <label
              key={option.value}
              className={`cursor-pointer rounded-[5px] px-2 py-1 text-center text-[13px] transition-colors has-focus-visible:outline-2 has-focus-visible:outline-accent ${
                target === option.value
                  ? "bg-raised font-medium text-ink shadow-[0_1px_2px_rgb(16_19_24/0.08)]"
                  : "text-muted hover:text-ink"
              }`}
            >
              <input
                type="radio"
                name="ingest-target"
                value={option.value}
                checked={target === option.value}
                onChange={() => setTarget(option.value)}
                className="sr-only"
              />
              {option.label}
            </label>
          ))}
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">{activeTarget.hint}</p>
      </fieldset>

      <div
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy}
        onClick={() => !busy && inputRef.current?.click()}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !busy) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`mt-3 flex cursor-pointer flex-col items-center rounded-md border border-dashed px-3 py-4 text-center transition-colors aria-disabled:cursor-not-allowed aria-disabled:opacity-60 ${
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-raised hover:border-ink-2"
        }`}
      >
        <Upload className="text-muted" width="18" height="18" />
        <p className="mt-1.5 text-[13px] text-ink">
          Drop files or <span className="font-medium text-accent">browse</span>
        </p>
        <p className="mt-0.5 text-[11px] text-muted">
          PDF, DOCX, JPG, PNG · up to {MAX_FILES_PER_REQUEST} files, 50 MB each
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={handlePick}
          className="hidden"
        />
      </div>

      {queue.length > 0 && (
        <ul className="mt-2 divide-y divide-line rounded-md border border-line bg-raised">
          {queue.map((file, index) => (
            <li key={`${file.name}-${file.size}`} className="flex items-center gap-2 py-1 pl-2.5 pr-1 text-[13px]">
              <span className="min-w-0 truncate text-ink" title={file.name}>{file.name}</span>
              <span className="tnum shrink-0 font-mono text-[11px] text-muted">{formatBytes(file.size)}</span>
              <button
                type="button"
                onClick={() => removeAt(index)}
                disabled={busy}
                aria-label={`Remove ${file.name}`}
                className="ml-auto shrink-0 rounded p-1 text-muted hover:bg-sunken hover:text-ink disabled:opacity-40"
              >
                <Close width="14" height="14" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {rejected.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {rejected.map((item) => (
            <li key={item.name} className="text-xs text-bad">
              {item.name}: {item.reason}
            </li>
          ))}
        </ul>
      )}

      {queue.length > 0 && (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={handleUpload}
            disabled={busy}
            className="flex-1 rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {busy ? "Ingesting" : `Ingest ${queue.length} file${queue.length === 1 ? "" : "s"}`}
          </button>
          {!busy && (
            <button
              type="button"
              onClick={() => {
                setQueue([]);
                setRejected([]);
              }}
              className="rounded-md border border-line-strong bg-raised px-3 py-1.5 text-[13px] text-ink-2 hover:bg-sunken hover:text-ink"
            >
              Clear
            </button>
          )}
        </div>
      )}

      <Status phase={phase} onDismiss={() => setPhase({ kind: "idle" })} />
    </div>
  );
}

function Status({ phase, onDismiss }: { phase: Phase; onDismiss: () => void }) {
  if (phase.kind === "idle") return null;

  if (phase.kind === "uploading") {
    const percent = Math.round(phase.fraction * 100);
    return (
      <div className="mt-3" role="status">
        <p className="tnum text-xs text-ink-2">Uploading {percent}%</p>
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-line">
          <div
            className="h-full bg-accent transition-[width] duration-150"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    );
  }

  if (phase.kind === "processing") {
    return (
      <div className="mt-3" role="status">
        <p className="text-xs text-ink-2">Extracting and indexing</p>
        <div className="skeleton mt-1 h-1 w-full" />
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
          Each figure costs a vision call, so a large batch takes a few minutes.
        </p>
      </div>
    );
  }

  const tone =
    phase.kind === "error" ? "border-bad/25 bg-bad-soft text-bad" : "border-good/20 bg-good-soft text-ink";

  return (
    <div role={phase.kind === "error" ? "alert" : "status"} className={`mt-3 flex gap-2 rounded-md border px-2.5 py-2 text-xs leading-relaxed ${tone}`}>
      <div className="min-w-0 flex-1">
        {phase.kind === "error" ? (
          phase.message
        ) : (
          <DoneSummary result={phase.result} />
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="-m-0.5 h-fit shrink-0 rounded p-0.5 opacity-60 hover:opacity-100"
      >
        <Close width="13" height="13" />
      </button>
    </div>
  );
}

function DoneSummary({ result }: { result: IngestResponse }) {
  const methods = Object.entries(result.indexed.by_extraction_method).filter(([, count]) => count > 0);
  return (
    <>
      <p>
        Indexed <span className="tnum font-medium">{result.indexed.total}</span> passage
        {result.indexed.total === 1 ? "" : "s"} from {result.succeeded.length} document
        {result.succeeded.length === 1 ? "" : "s"} into{" "}
        {result.corpus_scope === "persistent" ? "the corpus" : "this session"}.
      </p>
      {methods.length > 0 && (
        <p className="tnum mt-0.5 text-muted">
          {methods.map(([name, count]) => `${count} ${name}`).join(" · ")}
        </p>
      )}
      {result.failed.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-bad">
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
