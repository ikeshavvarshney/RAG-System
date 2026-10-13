"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import AnswerCard, { type AnswerState } from "@/components/AnswerCard";
import FileUpload from "@/components/FileUpload";
import { ArrowUp, Close, Doc, Stop } from "@/components/Icons";
import { applyStageEvent } from "@/components/StageProgress";
import { streamQuery, type IngestResponse } from "@/lib/api";

type Message =
  | { id: string; role: "user" | "note"; content: string }
  | { id: string; role: "answer"; state: AnswerState };

const EXAMPLES = [
  "What is the combined nameplate capacity of Nigeria's four state-owned refineries?",
  "How much installed electricity generation capacity did Indonesia have in 2023?",
  "What total budget did NASA request for fiscal year 2023?",
  "In the Census chart on age groups by region, how did the Under 18 population in the South change between April 2020 and July 2025?",
];

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    const field = inputRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
  }, [input]);

  function handleIngested(result: IngestResponse) {
    const where = result.corpus_scope === "persistent" ? "the corpus" : "this session";
    const lines = [
      `Indexed ${result.indexed.total} passages from ${result.succeeded.length} document(s) into ${where}: ${result.succeeded.join(", ")}.`,
    ];
    if (result.failed.length > 0) {
      lines.push(
        `Skipped ${result.failed.length}: ${result.failed
          .map((failure) => `${failure.filename} (${failure.reason})`)
          .join("; ")}.`,
      );
    }
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "note", content: lines.join("\n") },
    ]);
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || pending) return;

    const answerId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text },
      { id: answerId, role: "answer", state: { stages: [], usage: [] } },
    ]);
    setInput("");
    setPending(true);

    const update = (change: (state: AnswerState) => AnswerState) =>
      setMessages((prev) =>
        prev.map((message) =>
          message.id === answerId && message.role === "answer"
            ? { ...message, state: change(message.state) }
            : message,
        ),
      );

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await streamQuery(
        text,
        {
          onStage: (event) =>
            update((state) => ({ ...state, stages: applyStageEvent(state.stages, event) })),
          onUsage: (event) =>
            update((state) => ({ ...state, usage: [...state.usage, event] })),
        },
        controller.signal,
      );
      update((state) => ({ ...state, result }));
    } catch (error) {
      update((state) => ({
        ...state,
        error: controller.signal.aborted
          ? "Stopped before an answer arrived."
          : error instanceof Error
            ? error.message
            : String(error),
      }));
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask(input);
    }
  }

  const empty = messages.length === 0;

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1">
      <aside
        className={`${
          showDocs ? "fixed inset-0 top-14 z-20 flex" : "hidden"
        } w-full shrink-0 flex-col overflow-y-auto border-line bg-page px-4 py-5 sm:px-6 lg:static lg:flex lg:w-80 lg:border-r lg:px-5`}
        aria-label="Documents"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-lg font-medium tracking-tight">Documents</h2>
          <button
            type="button"
            onClick={() => setShowDocs(false)}
            aria-label="Close documents"
            className="rounded p-1.5 text-ink-2 hover:bg-sunken hover:text-ink lg:hidden"
          >
            <Close />
          </button>
        </div>
        <FileUpload onIngested={handleIngested} />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Conversation">
        <div className="flex shrink-0 items-center justify-end border-b border-line px-4 py-2 lg:hidden">
          <button
            type="button"
            onClick={() => setShowDocs(true)}
            className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink"
          >
            <Doc />
            Documents
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8">
            {empty ? (
              <div className="pt-[6vh]">
                <h1 className="text-balance font-serif text-3xl font-medium tracking-tight">
                  Ask about the indexed documents
                </h1>
                <p className="mt-3 max-w-[58ch] text-sm leading-relaxed text-ink-2">
                  Every answer cites the document and page it came from, or the
                  web page when the corpus could not answer. Documents uploaded
                  to this session are searched on their own.
                </p>

                <h2 className="mt-10 text-xs font-medium text-muted">Questions the corpus can answer</h2>
                <ul className="mt-2 divide-y divide-line border-y border-line">
                  {EXAMPLES.map((example) => (
                    <li key={example}>
                      <button
                        type="button"
                        onClick={() => void ask(example)}
                        disabled={pending}
                        className="group flex w-full items-baseline gap-3 py-3 text-left text-sm text-ink transition-colors hover:text-accent-strong"
                      >
                        <span className="font-serif text-[15px] leading-snug">{example}</span>
                        <span className="ml-auto shrink-0 text-xs text-muted group-hover:text-accent-strong">
                          Ask
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="space-y-8">
                {messages.map((message) => {
                  if (message.role === "answer") {
                    return <AnswerCard key={message.id} id={message.id} state={message.state} />;
                  }
                  if (message.role === "note") {
                    return (
                      <p
                        key={message.id}
                        className="whitespace-pre-wrap border-l border-line-strong pl-3 text-xs leading-relaxed text-ink-2"
                      >
                        {message.content}
                      </p>
                    );
                  }
                  return (
                    <h2
                      key={message.id}
                      className="whitespace-pre-wrap text-pretty border-t border-line pt-6 font-serif text-xl font-medium leading-snug tracking-tight first:border-t-0 first:pt-0"
                    >
                      {message.content}
                    </h2>
                  );
                })}
              </div>
            )}
            <div ref={bottomRef} className="h-6" />
          </div>
        </div>

        <form onSubmit={handleSubmit} className="shrink-0 px-4 pb-4 sm:px-8">
          <div className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-xl border border-line-strong bg-raised p-2 shadow-[0_1px_3px_rgb(11_11_11/0.06)] transition-colors focus-within:border-ink">
            <label htmlFor="question" className="sr-only">
              Your question
            </label>
            <textarea
              id="question"
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your documents"
              className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-muted"
            />
            {pending ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                aria-label="Stop"
                className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-line-strong text-ink hover:bg-sunken"
              >
                <Stop />
              </button>
            ) : (
              <button
                type="submit"
                disabled={input.trim() === ""}
                aria-label="Send"
                className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ink text-white transition-colors hover:bg-ink/85 disabled:bg-sunken disabled:text-muted"
              >
                <ArrowUp />
              </button>
            )}
          </div>
          <p className="mx-auto mt-1.5 hidden w-full max-w-3xl px-1 text-[11px] text-muted sm:block">
            Enter to send, Shift and Enter for a new line.
          </p>
        </form>
      </section>
    </div>
  );
}
