import type { AnswerState } from "@/components/AnswerCard";

export type ChatEntry =
  | { kind: "turn"; id: string; question: string; state: AnswerState }
  | { kind: "upload"; id: string; files: string[]; at: number };

/** One conversation. Its id is the backend session id that scopes its uploads and follow-up context. */
export interface ChatRecord {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  entries: ChatEntry[];
}

const CHATS_KEY = "rag.chats";
const MAX_CHATS = 50;
const TITLE_CHARS = 60;

export const INTERRUPTED = "Interrupted before an answer arrived.";

export function chatTitle(entries: ChatEntry[]): string {
  const first = entries.find((entry) => entry.kind === "turn");
  if (first) return first.question.slice(0, TITLE_CHARS);
  const upload = entries.find((entry) => entry.kind === "upload");
  return upload ? `Uploaded ${upload.files.join(", ")}`.slice(0, TITLE_CHARS) : "New chat";
}

export function loadChats(): ChatRecord[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(CHATS_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return (parsed as ChatRecord[]).map((chat) => ({ ...chat, entries: chat.entries.map(settle) }));
  } catch {
    return [];
  }
}

/** A turn saved mid-stream can never finish, so it comes back as interrupted. */
function settle(entry: ChatEntry): ChatEntry {
  if (entry.kind !== "turn" || entry.state.result || entry.state.error) return entry;
  return { ...entry, state: { ...entry.state, error: INTERRUPTED } };
}

/** Write the list, dropping the oldest chats until it fits the storage quota. */
export function saveChats(chats: ChatRecord[]): ChatRecord[] {
  let kept = [...chats].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CHATS);
  while (kept.length > 0) {
    try {
      localStorage.setItem(CHATS_KEY, JSON.stringify(kept));
      return kept;
    } catch {
      kept = kept.slice(0, -1);
    }
  }
  return kept;
}

export function upsertChat(chats: ChatRecord[], id: string, entries: ChatEntry[]): ChatRecord[] {
  const now = Date.now();
  const existing = chats.find((chat) => chat.id === id);
  const record: ChatRecord = {
    id,
    title: chatTitle(entries),
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    entries,
  };
  return [record, ...chats.filter((chat) => chat.id !== id)];
}
