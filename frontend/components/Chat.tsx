"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import AnswerCard, { type AnswerState } from "@/components/AnswerCard";
import ChatList from "@/components/ChatList";
import CorpusDocuments from "@/components/CorpusDocuments";
import DocumentViewer, { ActiveViewerContext, ViewerContext } from "@/components/DocumentViewer";
import { AttachButton, AttachStatus, useAttachments } from "@/components/Attachments";
import { ArrowUp, Close, Doc, Plus, Stop } from "@/components/Icons";
import SessionDocuments from "@/components/SessionDocuments";
import { applyStageEvent } from "@/components/StageProgress";
import { useDocuments } from "@/components/useDocuments";
import {
  currentSessionId,
  deleteSession,
  getSessionId,
  setCurrentSessionId,
  streamQuery,
  type IngestResponse,
  type IngestTarget,
} from "@/lib/api";
import { loadChats, saveChats, upsertChat, type ChatEntry, type ChatRecord } from "@/lib/chats";
import type { ViewerTarget } from "@/lib/viewer";

type Turn = Extract<ChatEntry, { kind: "turn" }>;

const EXAMPLES = [
  "What is the combined nameplate capacity of Nigeria's four state-owned refineries?",
  "How much installed electricity generation capacity did Indonesia have in 2023?",
  "What total budget did NASA request for fiscal year 2023?",
  "In the Census chart on age groups by region, how did the Under 18 population in the South change between April 2020 and July 2025?",
];

const EMPTY_STATE: AnswerState = { stages: [], usage: [] };
const STOPPED = "Stopped before an answer arrived.";

function stopPending(entries: ChatEntry[]): ChatEntry[] {
  return entries.map((entry) =>
    entry.kind === "turn" && !entry.state.result && !entry.state.error
      ? { ...entry, state: { ...entry.state, error: STOPPED } }
      : entry,
  );
}

export default function Chat() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [chats, setChats] = useState<ChatRecord[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [showSidebar, setShowSidebar] = useState(false);
  const [viewing, setViewing] = useState<ViewerTarget | null>(null);
  const [resetError, setResetError] = useState<string | null>(null);
  const { corpus, session, offline, refresh } = useDocuments();
  const attachments = useAttachments(handleIngested);
  const [dragging, setDragging] = useState(false);

  const openViewer = useCallback((target: ViewerTarget) => {
    setShowSidebar(false);
    setViewing(target);
  }, []);
  const closeViewer = useCallback(() => setViewing(null), []);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const stored = loadChats();
    const current = currentSessionId();
    const chat = stored.find((item) => item.id === current);
    setChats(stored);
    setActiveId(current);
    setEntries(chat?.entries ?? []);
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded || !activeId || entries.length === 0) return;
    setChats((prev) =>
      prev.find((chat) => chat.id === activeId)?.entries === entries ? prev : saveChats(upsertChat(prev, activeId, entries)),
    );
  }, [loaded, activeId, entries]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries]);

  useEffect(() => {
    const field = inputRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
  }, [input]);

  async function run(turnId: string, question: string) {
    const update = (change: (state: AnswerState) => AnswerState) =>
      setEntries((prev) =>
        prev.map((entry) =>
          entry.kind === "turn" && entry.id === turnId ? { ...entry, state: change(entry.state) } : entry,
        ),
      );

    setPending(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setActiveId(await getSessionId());
      const result = await streamQuery(
        question,
        {
          onStage: (event) =>
            update((state) => ({ ...state, stages: applyStageEvent(state.stages, event) })),
          onUsage: (event) =>
            update((state) => ({ ...state, usage: [...state.usage, event] })),
          onModelStatus: (event) =>
            update((state) => ({
              ...state,
              modelStatus: state.modelStatus?.reason === "overloaded" && event.reason === "slow" ? state.modelStatus : event,
            })),
        },
        controller.signal,
      );
      update((state) => ({ ...state, result }));
    } catch (error) {
      update((state) => ({
        ...state,
        error: controller.signal.aborted ? STOPPED : error instanceof Error ? error.message : String(error),
      }));
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setPending(false);
        inputRef.current?.focus();
      }
    }
  }

  function ask(question: string) {
    const text = question.trim();
    if (!text || pending || attachments.busy) return;
    const id = crypto.randomUUID();
    setEntries((prev) => [...prev, { kind: "turn", id, question: text, state: EMPTY_STATE }]);
    setInput("");
    void run(id, text);
  }

  function retry(turn: Turn) {
    if (pending) return;
    setEntries((prev) =>
      prev.map((entry) => (entry.kind === "turn" && entry.id === turn.id ? { ...entry, state: EMPTY_STATE } : entry)),
    );
    void run(turn.id, turn.question);
  }

  // Saves a running answer as stopped, so the chat left behind is not stuck mid-stream.
  function leaveChat() {
    if (!abortRef.current) return;
    abortRef.current.abort();
    abortRef.current = null;
    setPending(false);
    if (activeId && entries.length > 0) {
      setChats((prev) => saveChats(upsertChat(prev, activeId, stopPending(entries))));
    }
  }

  function openChat(id: string | null) {
    setShowSidebar(false);
    setResetError(null);
    if (id !== null && id === activeId) return;
    leaveChat();
    setCurrentSessionId(id);
    setActiveId(id);
    setEntries(chats.find((chat) => chat.id === id)?.entries ?? []);
    setInput("");
    setViewing(null);
    refresh();
    inputRef.current?.focus();
  }

  async function removeChat(id: string) {
    if (!window.confirm("Delete this chat and the documents uploaded to it?")) return;
    if (id === activeId) openChat(null);
    setChats((prev) => saveChats(prev.filter((chat) => chat.id !== id)));
    try {
      await deleteSession(id);
    } catch (error) {
      setResetError(error instanceof Error ? error.message : String(error));
    }
    refresh();
  }

  function handleIngested(result: IngestResponse, target: IngestTarget, sessionId: string | undefined) {
    refresh();
    if (target !== "session" || !sessionId || result.succeeded.length === 0) return;
    const entry: ChatEntry = { kind: "upload", id: crypto.randomUUID(), files: result.succeeded, at: Date.now() };
    if (sessionId === currentSessionId()) {
      setActiveId(sessionId);
      setEntries((prev) => [...prev, entry]);
    } else {
      // The user switched chats while the upload ran.
      setChats((prev) =>
        saveChats(upsertChat(prev, sessionId, [...(prev.find((chat) => chat.id === sessionId)?.entries ?? []), entry])),
      );
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    ask(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      ask(input);
    }
  }

  const empty = entries.length === 0;
  const sessionScoped = session.length > 0;
  const scopeLabel = sessionScoped
    ? `This chat · ${session.length} document${session.length === 1 ? "" : "s"}`
    : corpus
      ? `Corpus · ${corpus.length} document${corpus.length === 1 ? "" : "s"}`
      : "Corpus";

  return (
    <ViewerContext.Provider value={openViewer}>
      <ActiveViewerContext.Provider value={viewing}>
      <div className="flex min-h-0 w-full flex-1">
        <aside
          className={`${
            showSidebar ? "fixed inset-0 top-12 z-20 flex" : "hidden"
          } w-full shrink-0 flex-col overflow-y-auto border-line bg-surface px-3 py-4 ${
            viewing ? "xl:static xl:flex xl:w-72 xl:border-r" : "lg:static lg:flex lg:w-72 lg:border-r"
          }`}
          aria-label="Chats and sources"
        >
          <div className={`mb-3 flex justify-end ${viewing ? "xl:hidden" : "lg:hidden"}`}>
            <button
              type="button"
              onClick={() => setShowSidebar(false)}
              aria-label="Close sidebar"
              className="rounded p-1 text-ink-2 hover:bg-sunken hover:text-ink"
            >
              <Close />
            </button>
          </div>
          <ChatList
            chats={chats}
            activeId={activeId}
            onSelect={openChat}
            onNew={() => openChat(null)}
            onDelete={removeChat}
          />
          <h2 className="mt-6 border-t border-line px-1 pt-4 text-xs font-medium text-muted">Sources</h2>
          <SessionDocuments documents={session} onChanged={refresh} />
          <CorpusDocuments documents={corpus} offline={offline} onChanged={refresh} />
        </aside>

        <section className="flex min-w-0 flex-1 flex-col bg-raised" aria-label="Conversation">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-line px-3 sm:px-4">
            <button
              type="button"
              onClick={() => {
                setViewing(null);
                setShowSidebar(true);
              }}
              className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] text-ink-2 hover:bg-sunken hover:text-ink ${
                viewing ? "xl:hidden" : "lg:hidden"
              }`}
            >
              <Doc width="15" height="15" />
              Chats & sources
            </button>
            <p className="flex min-w-0 items-center gap-2 text-xs text-muted">
              <span className="hidden sm:inline">Answering from</span>
              <span
                className={`truncate rounded-full px-2 py-0.5 font-medium ${
                  sessionScoped ? "bg-accent-soft text-accent-strong" : "bg-sunken text-ink-2"
                }`}
              >
                {scopeLabel}
              </span>
            </p>
            <button
              type="button"
              onClick={() => openChat(null)}
              disabled={empty && !pending}
              className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line-strong px-2.5 py-1 text-[13px] text-ink hover:bg-sunken disabled:border-line disabled:text-muted disabled:hover:bg-transparent lg:hidden"
            >
              <Plus width="14" height="14" />
              New chat
            </button>
          </div>

          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-190 px-4 py-6 sm:px-8">
              {resetError && (
                <p role="alert" className="mb-4 rounded-md border border-bad/25 bg-bad-soft px-3 py-2 text-sm text-bad">
                  {resetError}
                </p>
              )}
              {empty ? (
                <EmptyState
                  pending={pending}
                  corpusEmpty={corpus !== null && corpus.length === 0 && !sessionScoped}
                  onAsk={ask}
                />
              ) : (
                <div className="space-y-10">
                  {entries.map((entry) =>
                    entry.kind === "upload" ? (
                      <p key={entry.id} className="flex items-start gap-2 text-xs leading-relaxed text-muted">
                        <Doc className="mt-0.5 shrink-0" width="14" height="14" />
                        <span>
                          Uploaded to this chat: <span className="text-ink-2">{entry.files.join(", ")}</span>
                        </span>
                      </p>
                    ) : (
                      <div key={entry.id} className="animate-rise">
                        <p className="whitespace-pre-wrap text-pretty rounded-lg bg-sunken px-4 py-2.5 text-[15px] font-medium leading-snug">
                          {entry.question}
                        </p>
                        <div className="mt-4">
                          <AnswerCard
                            id={entry.id}
                            state={entry.state}
                            onRetry={pending ? undefined : () => retry(entry)}
                          />
                        </div>
                      </div>
                    ),
                  )}
                </div>
              )}
              <div ref={bottomRef} className="h-4" />
            </div>
          </div>

          <form onSubmit={handleSubmit} className="shrink-0 px-4 pb-3 sm:px-8">
            <div
              onDragOver={(event) => {
                event.preventDefault();
                if (!attachments.busy) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                void attachments.upload(event.dataTransfer.files, "session");
              }}
              className={`mx-auto w-full max-w-190 rounded-lg border bg-raised p-1.5 shadow-[0_2px_8px_rgb(16_19_24/0.06)] transition-colors focus-within:border-accent ${
                dragging ? "border-accent bg-accent-soft" : "border-line-strong"
              }`}
            >
              <AttachStatus phase={attachments.phase} rejected={attachments.rejected} onDismiss={attachments.dismiss} />
              <div className="flex items-end gap-1.5">
                <AttachButton disabled={attachments.busy} onPick={(files, target) => void attachments.upload(files, target)} />
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
                  placeholder={sessionScoped ? "Ask about this chat's documents" : "Ask about the corpus"}
                  className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-muted focus-visible:outline-none"
                />
                {pending ? (
                  <button
                    type="button"
                    onClick={() => abortRef.current?.abort()}
                    aria-label="Stop"
                    title="Stop"
                    className="flex size-8 shrink-0 items-center justify-center rounded-md border border-line-strong text-ink hover:bg-sunken"
                  >
                    <Stop />
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={input.trim() === "" || attachments.busy}
                    aria-label="Send"
                    title={attachments.busy ? "Wait for the upload to finish" : "Send"}
                    className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-white transition-colors hover:bg-accent-strong disabled:bg-sunken disabled:text-muted"
                  >
                    <ArrowUp />
                  </button>
                )}
              </div>
            </div>
            <p className="mx-auto mt-1.5 hidden w-full max-w-190 px-1 text-[11px] text-muted sm:block">
              Enter to send · Shift+Enter for a new line · follow-up questions use the last three turns as context
            </p>
          </form>
        </section>

        {viewing && (
          <DocumentViewer
            key={`${viewing.sourceDoc}:${viewing.chunkId ?? ""}:${viewing.page ?? ""}`}
            target={viewing}
            onClose={closeViewer}
          />
        )}
      </div>
      </ActiveViewerContext.Provider>
    </ViewerContext.Provider>
  );
}

function EmptyState({
  pending,
  corpusEmpty,
  onAsk,
}: {
  pending: boolean;
  corpusEmpty: boolean;
  onAsk: (question: string) => void;
}) {
  return (
    <div className="pt-[4vh]">
      <h1 className="text-2xl font-semibold tracking-tight">Ask the documents</h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-ink-2">
        Answers cite the document page they came from. Click a citation to open the page with the
        passage highlighted. When the documents cannot answer, the system searches the web and
        cites those pages instead.
      </p>

      {corpusEmpty && (
        <div className="mt-6 rounded-md border border-warn/30 bg-warn-soft px-3 py-2.5 text-sm text-warn">
          Nothing is indexed yet, so questions can only be answered from the web. Add documents with
          the + button in the message box.
        </div>
      )}

      <h2 className="mt-8 text-xs font-medium text-muted">Try a question from the reference corpus</h2>
      <ul className="mt-2 overflow-hidden rounded-lg border border-line">
        {EXAMPLES.map((example) => (
          <li key={example} className="border-b border-line last:border-b-0">
            <button
              type="button"
              onClick={() => onAsk(example)}
              disabled={pending}
              className="group flex w-full items-center gap-3 px-3.5 py-2.5 text-left text-sm text-ink transition-colors hover:bg-surface disabled:opacity-50"
            >
              <span className="leading-snug">{example}</span>
              <ArrowUp className="ml-auto shrink-0 rotate-45 text-muted transition-colors group-hover:text-accent" width="14" height="14" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
