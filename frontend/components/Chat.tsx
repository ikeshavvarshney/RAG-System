"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import FileUpload from "@/components/FileUpload";
import { checkHealth, type IngestResponse } from "@/lib/api";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

const GREETING: Message = {
  id: "greeting",
  role: "assistant",
  content:
    "Upload documents above to index them. Retrieval is not wired up yet, so I cannot answer from them yet; ask anything and I will report live backend status instead.",
};

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([GREETING]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [showUpload, setShowUpload] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function say(content: string) {
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "assistant", content },
    ]);
  }

  /** Record the ingest in the transcript so it survives hiding the panel. */
  function handleIngested(result: IngestResponse) {
    const names = result.succeeded.join(", ");
    const where =
      result.corpus_scope === "persistent" ? "the corpus" : "this session";
    const lines = [
      `Indexed ${result.indexed.total} passages from ${result.succeeded.length} document(s) into ${where}: ${names}.`,
    ];
    if (result.failed.length > 0) {
      lines.push(
        `Skipped ${result.failed.length}: ${result.failed
          .map((failure) => `${failure.filename} (${failure.reason})`)
          .join("; ")}.`,
      );
    }
    say(lines.join("\n"));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || pending) return;

    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text },
    ]);
    setInput("");
    setPending(true);

    let reply: string;
    try {
      const health = await checkHealth();
      reply = `Backend reachable (status ${health.status}, version ${health.version}). The retrieval pipeline is not connected yet, so I cannot answer "${text}".`;
    } catch (error) {
      reply = `Backend unreachable: ${
        error instanceof Error ? error.message : String(error)
      }`;
    }

    say(reply);
    setPending(false);
  }

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between px-4 pt-3">
        <h2 className="text-sm font-medium text-neutral-500">Documents</h2>
        <button
          type="button"
          onClick={() => setShowUpload((open) => !open)}
          className="text-xs text-neutral-500 hover:text-neutral-900"
        >
          {showUpload ? "Hide" : "Add documents"}
        </button>
      </div>

      {showUpload && <FileUpload onIngested={handleIngested} />}

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-6">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
                message.role === "user"
                  ? "bg-neutral-900 text-white"
                  : "bg-neutral-100 text-neutral-800"
              }`}
            >
              {message.content}
            </div>
          </div>
        ))}
        {pending && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-neutral-100 px-4 py-2 text-sm text-neutral-500">
              thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex shrink-0 items-center gap-2 border-t border-neutral-200 bg-white px-4 py-3"
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask about your documents..."
          className="flex-1 rounded-full border border-neutral-300 px-4 py-2 text-sm outline-none focus:border-neutral-900"
        />
        <button
          type="submit"
          disabled={pending || input.trim() === ""}
          className="rounded-full bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
