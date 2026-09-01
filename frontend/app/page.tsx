import Chat from "@/components/Chat";
import HealthBadge from "@/components/HealthBadge";

export default function Home() {
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
        <h1 className="text-lg font-semibold tracking-tight">Multimodal RAG</h1>
        <HealthBadge />
      </header>
      <Chat />
    </main>
  );
}
