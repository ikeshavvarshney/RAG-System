"use client";

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Close, Upload } from "@/components/Icons";
import SessionDocuments from "@/components/SessionDocuments";
import {
  ACCEPTED_EXTENSIONS,
  MAX_FILES_PER_REQUEST,
  deleteSession,
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
  onIngested?: (result: IngestResponse) => void;
}

const TARGETS: { value: IngestTarget; label: string; hint: string }[] = [
  {
    value: "corpus",
    label: "Corpus",
    hint: "Stored permanently and shared by everyone using this system.",
  },
  {
    value: "session",
    label: "This session",
    hint: "Kept in a separate store, never mixed into the corpus, and discarded when the session ends.",
  },
];

export default function FileUpload({ onIngested }: Props) {
  const [queue, setQueue] = useState<File[]>([]);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);
  const [target, setTarget] = useState<IngestTarget>("corpus");
  const [clearing, setClearing] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
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
      } else if (!queue.some((q) => q.name === file.name && q.size === file.size)) {
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
      setRefreshToken((token) => token + 1);
      onIngested?.(result);
    } catch (error) {
      setPhase({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleClearSession() {
    setClearing(true);
    try {
      await deleteSession();
      setPhase({ kind: "idle" });
      setRefreshToken((token) => token + 1);
    } catch (error) {
      setPhase({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setClearing(false);
    }
  }

  const activeTarget = TARGETS.find((option) => option.value === target)!;

  return (
    <div>
      <fieldset disabled={busy}>
        <legend className="text-xs font-medium text-ink-2">Add to</legend>
        <div className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-sunken p-1">
          {TARGETS.map((option) => (
            <label
              key={option.value}
              className={`cursor-pointer rounded-md px-2 py-1.5 text-center text-sm transition-colors has-focus-visible:outline-2 has-focus-visible:outline-accent ${
                target === option.value
                  ? "bg-raised font-medium text-ink shadow-[0_1px_2px_rgb(11_11_11/0.08)]"
                  : "text-ink-2 hover:text-ink"
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
        <p className="mt-2 text-xs leading-relaxed text-muted">{activeTarget.hint}</p>
      </fieldset>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`mt-4 rounded-lg border border-dashed px-4 py-6 text-center transition-colors ${
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface"
        }`}
      >
        <Upload className="mx-auto mb-2 text-muted" width="18" height="18" />
        <p className="text-sm text-ink-2">
          Drop documents here, or{" "}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
            className="font-medium text-accent-strong underline hover:text-ink disabled:opacity-40"
          >
            choose files
          </button>
        </p>
        <p className="mt-1 text-xs text-muted">
          PDF, DOCX, JPG, PNG. Up to {MAX_FILES_PER_REQUEST} files, 50 MB each.
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
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {queue.map((file, index) => (
            <li key={`${file.name}-${file.size}`} className="flex items-center gap-2 py-1.5 text-sm">
              <span className="truncate text-ink">{file.name}</span>
              <span className="tnum shrink-0 text-xs text-muted">{formatBytes(file.size)}</span>
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
        <ul className="mt-3 space-y-1">
          {rejected.map((item) => (
            <li key={item.name} className="text-xs text-bad">
              {item.name}: {item.reason}
            </li>
          ))}
        </ul>
      )}

      {queue.length > 0 && (
        <button
          type="button"
          onClick={handleUpload}
          disabled={busy}
          className="mt-3 w-full rounded-md bg-ink px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-ink/85 disabled:opacity-40"
        >
          {busy
            ? "Working"
            : `Ingest ${queue.length} file${queue.length === 1 ? "" : "s"} into ${
                target === "corpus" ? "the corpus" : "this session"
              }`}
        </button>
      )}

      <Status phase={phase} />

      {target === "session" && (
        <>
          <SessionDocuments
            refreshToken={refreshToken}
            onChanged={() => setPhase({ kind: "idle" })}
          />
          <button
            type="button"
            onClick={handleClearSession}
            disabled={busy || clearing}
            className="mt-3 text-xs text-ink-2 underline decoration-line-strong hover:text-ink disabled:opacity-40"
          >
            {clearing ? "Clearing" : "Clear session uploads"}
          </button>
        </>
      )}
    </div>
  );
}

function Status({ phase }: { phase: Phase }) {
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
      <p className="mt-3 flex gap-2 text-xs leading-relaxed text-ink-2" role="status">
        <span className="mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full bg-accent" />
        Extracting and indexing. Figures each cost a vision call, so this can
        take a few minutes.
      </p>
    );
  }

  if (phase.kind === "error") {
    return (
      <p role="alert" className="mt-3 text-xs text-bad">
        {phase.message}
      </p>
    );
  }

  const { result } = phase;
  const methods = Object.entries(result.indexed.by_extraction_method).filter(
    ([, count]) => count > 0,
  );

  return (
    <div className="mt-3 text-xs leading-relaxed">
      <p className="text-ink">
        Indexed {result.indexed.total} passage
        {result.indexed.total === 1 ? "" : "s"} from {result.succeeded.length} document
        {result.succeeded.length === 1 ? "" : "s"} into{" "}
        {result.corpus_scope === "persistent" ? "the corpus" : "this session"}
        {methods.length > 0 && (
          <span className="text-muted">
            {" "}
            ({methods.map(([name, count]) => `${count} ${name}`).join(", ")})
          </span>
        )}
        .
      </p>

      {result.failed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {result.failed.map((failure) => (
            <li key={failure.filename} className="text-bad">
              {failure.filename}: {failure.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
