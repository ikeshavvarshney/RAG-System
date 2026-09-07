import Link from "next/link";

const PIPELINE = [
  {
    step: "Extract",
    body: "PDFs, DOCX files and images are routed by their magic bytes, not their extension. Pages with a text layer are read directly. Pages that are really scans or full-page figures are rendered and sent to a vision model, which transcribes tables as markdown and describes charts by their axes and values. If that fails, OCR takes over.",
  },
  {
    step: "Chunk",
    body: "Passages are split at headings and paragraph breaks rather than fixed offsets, targeting 500 tokens. Tables are never split, because half a table answers nothing. Each passage keeps its source document, page number and nearest heading.",
  },
  {
    step: "Index",
    body: "Every passage is embedded for dense retrieval and added to a BM25 keyword index. Two indexes over one set of passages: semantic search handles paraphrase, keyword search handles exact identifiers and proper nouns.",
  },
  {
    step: "Answer",
    body: "A question is answered from retrieved passages with inline citations, each traceable to a document and a page.",
  },
];

export default function Home() {
  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-14">
        <h1 className="text-2xl font-semibold tracking-tight">
          Ask questions about your documents
        </h1>

        <p className="mt-4 text-sm leading-relaxed text-neutral-600">
          A retrieval-augmented generation system for documents whose content is
          not only text. Most of what a report actually says often sits in its
          tables and charts, where ordinary text extraction either drops it or
          flattens it into a row of numbers with no columns attached. This
          system reads that content with a vision model and keeps its structure,
          so a question whose answer lives in a table is still answerable.
        </p>

        <div className="mt-8 flex items-center gap-4">
          <Link
            href="/chat"
            className="rounded-full bg-neutral-900 px-5 py-2 text-sm font-medium text-white hover:bg-neutral-700"
          >
            Open the chat
          </Link>
          <span className="text-sm text-neutral-500">
            Upload documents there to index them.
          </span>
        </div>

        <section className="mt-14">
          <h2 className="text-sm font-semibold tracking-tight">
            How a document becomes an answer
          </h2>

          <ol className="mt-5 space-y-5">
            {PIPELINE.map((stage, index) => (
              <li key={stage.step} className="flex gap-4">
                <span className="mt-0.5 w-5 shrink-0 text-sm tabular-nums text-neutral-400">
                  {index + 1}
                </span>
                <div>
                  <h3 className="text-sm font-medium">{stage.step}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-neutral-600">
                    {stage.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-12 rounded-lg border border-neutral-200 px-5 py-4">
          <h2 className="text-sm font-semibold tracking-tight">
            What works today
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-neutral-600">
            Ingestion runs end to end: upload documents on the chat page and
            they are extracted, chunked and indexed. Retrieval and answer
            generation are not connected yet, so the chat will not answer
            questions from your corpus so far. Ingestion is slow by nature,
            since every figure and scanned page costs a vision call, so expect
            minutes rather than seconds for a large batch.
          </p>
        </section>

        <p className="mt-10 text-sm text-neutral-500">
          Accepts PDF, DOCX, JPG and PNG. Up to 60 files per upload, 50 MB each.
        </p>
      </div>
    </main>
  );
}
