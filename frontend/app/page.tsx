export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 px-6">
      <h1 className="text-3xl font-semibold tracking-tight">Multimodal RAG</h1>
      <p className="text-neutral-600">
        Chat UI ships in the frontend track. Backend health check lives at{" "}
        <code className="rounded bg-neutral-100 px-1.5 py-0.5 text-sm">
          /api/health
        </code>
        .
      </p>
    </main>
  );
}