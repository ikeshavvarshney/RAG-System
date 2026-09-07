"use client";

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
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
    label: "This session only",
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
      } else if (
        !queue.some((q) => q.name === file.name && q.size === file.size)
      ) {
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
          // After the bytes land, the wait is extraction and embedding, which
          // report nothing. The label has to change or the page reads as stalled.
          setPhase(
            fraction >= 1 ? { kind: "processing" } : { kind: "uploading", fraction },
          );
        },
        sessionId,
      );
      setQueue([]);
      setRejected([]);
      setPhase({ kind: "done", result });
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
    <div className="border-b border-neutral-200 px-4 py-4">
      <fieldset className="mb-3" disabled={busy}>
        <legend className="sr-only">Where to store these documents</legend>
        <div className="flex items-center gap-4">
          {TARGETS.map((option) => (
            <label
              key={option.value}
              className="flex items-center gap-1.5 text-sm text-neutral-700"
            >
              <input
                type="radio"
                name="ingest-target"
                value={option.value}
                checked={target === option.value}
                onChange={() => setTarget(option.value)}
                className="accent-neutral-900"
              />
              {option.label}
            </label>
          ))}

          {target === "session" && (
            <button
              type="button"
              onClick={handleClearSession}
              disabled={busy || clearing}
              className="ml-auto text-xs text-neutral-500 hover:text-neutral-900 disabled:opacity-40"
            >
              {clearing ? "Clearing..." : "Clear session uploads"}
            </button>
          )}
        </div>
        <p className="mt-1 text-xs text-neutral-400">{activeTarget.hint}</p>
      </fieldset>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`rounded-lg border border-dashed px-4 py-6 text-center ${
          dragging ? "border-neutral-900 bg-neutral-50" : "border-neutral-300"
        }`}
      >
        <p className="text-sm text-neutral-600">
          Drop documents here, or{" "}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
            className="underline underline-offset-2 hover:text-neutral-900 disabled:opacity-40"
          >
            choose files
          </button>
        </p>
        <p className="mt-1 text-xs text-neutral-400">
          PDF, DOCX, JPG, PNG &middot; up to {MAX_FILES_PER_REQUEST} files
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
        <ul className="mt-3 space-y-1">
          {queue.map((file, index) => (
            <li
              key={`${file.name}-${file.size}`}
              className="flex items-center gap-3 text-sm"
            >
              <span className="truncate text-neutral-700">{file.name}</span>
              <span className="shrink-0 text-xs text-neutral-400">
                {formatBytes(file.size)}
              </span>
              <button
                type="button"
                onClick={() => removeAt(index)}
                disabled={busy}
                aria-label={`Remove ${file.name}`}
                className="ml-auto shrink-0 text-xs text-neutral-400 hover:text-neutral-900 disabled:opacity-40"
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {rejected.length > 0 && (
        <ul className="mt-3 space-y-1">
          {rejected.map((item) => (
            <li key={item.name} className="text-sm text-red-600">
              {item.name} &mdash; {item.reason}
            </li>
          ))}
        </ul>
      )}

      {queue.length > 0 && (
        <button
          type="button"
          onClick={handleUpload}
          disabled={busy}
          className="mt-3 rounded-full bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy
            ? "Working..."
            : `Ingest ${queue.length} file${queue.length === 1 ? "" : "s"} into ${
                target === "corpus" ? "the corpus" : "this session"
              }`}
        </button>
      )}

      <Status phase={phase} />
    </div>
  );
}

function Status({ phase }: { phase: Phase }) {
  if (phase.kind === "idle") return null;

  if (phase.kind === "uploading") {
    return (
      <div className="mt-3">
        <p className="text-sm text-neutral-600">
          Uploading {Math.round(phase.fraction * 100)}%
        </p>
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-neutral-200">
          <div
            className="h-full bg-neutral-900 transition-[width] duration-150"
            style={{ width: `${Math.round(phase.fraction * 100)}%` }}
          />
        </div>
      </div>
    );
  }

  if (phase.kind === "processing") {
    return (
      <p className="mt-3 text-sm text-neutral-600">
        Extracting and indexing. Figures and scanned pages each cost a vision
        call, so this can take a few minutes.
      </p>
    );
  }

  if (phase.kind === "error") {
    return <p className="mt-3 text-sm text-red-600">{phase.message}</p>;
  }

  const { result } = phase;
  const methods = Object.entries(result.indexed.by_extraction_method).filter(
    ([, count]) => count > 0,
  );

  return (
    <div className="mt-3 text-sm">
      <p className="text-neutral-700">
        Indexed {result.indexed.total} passage
        {result.indexed.total === 1 ? "" : "s"} from{" "}
        {result.succeeded.length} document
        {result.succeeded.length === 1 ? "" : "s"} into{" "}
        {result.corpus_scope === "persistent" ? "the corpus" : "this session"}
        {methods.length > 0 && (
          <span className="text-neutral-500">
            {" "}
            ({methods.map(([name, count]) => `${count} ${name}`).join(", ")})
          </span>
        )}
        .
      </p>

      {result.failed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {result.failed.map((failure) => (
            <li key={failure.filename} className="text-red-600">
              {failure.filename} &mdash; {failure.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
