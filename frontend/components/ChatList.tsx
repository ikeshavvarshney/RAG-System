"use client";

import { Plus, Trash } from "@/components/Icons";
import type { ChatRecord } from "@/lib/chats";

interface Props {
  chats: ChatRecord[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

const DAY_MS = 86_400_000;

function when(timestamp: number): string {
  const date = new Date(timestamp);
  if (Date.now() - timestamp < DAY_MS && date.getDate() === new Date().getDate()) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function ChatList({ chats, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <nav aria-label="Chats" className="flex min-h-0 flex-1 flex-col">
      <button
        type="button"
        onClick={onNew}
        className="inline-flex items-center justify-center gap-1.5 rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-[13px] text-ink hover:bg-sunken"
      >
        <Plus width="14" height="14" />
        New chat
      </button>

      <h2 className="mb-1.5 mt-5 px-1 text-xs font-medium text-muted">Chats</h2>
      {chats.length === 0 ? (
        <p className="px-1 text-xs leading-relaxed text-muted">Your conversations are kept in this browser and listed here.</p>
      ) : (
        <ul className="-mx-1 min-h-0 flex-1 space-y-px overflow-y-auto">
          {chats.map((chat) => {
            const active = chat.id === activeId;
            return (
              <li key={chat.id} className={`group flex items-center rounded-md ${active ? "bg-accent-soft" : "hover:bg-sunken"}`}>
                <button
                  type="button"
                  onClick={() => onSelect(chat.id)}
                  aria-current={active ? "page" : undefined}
                  title={chat.title}
                  className="min-w-0 flex-1 px-2 py-1.5 text-left"
                >
                  <span className={`block truncate text-[13px] ${active ? "font-medium text-accent-strong" : "text-ink"}`}>
                    {chat.title}
                  </span>
                  <span className="tnum block text-[11px] text-muted">{when(chat.updatedAt)}</span>
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(chat.id)}
                  aria-label={`Delete chat: ${chat.title}`}
                  title="Delete chat"
                  className="mr-1 shrink-0 rounded p-1 text-muted opacity-0 hover:bg-raised hover:text-bad focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                >
                  <Trash width="14" height="14" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
