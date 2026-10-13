import Link from "next/link";
import { ArrowRight } from "@/components/Icons";

const PIPELINE = [
  {
    step: "Extract",
    body: "PDFs, DOCX files and images are routed by their magic bytes, not their extension. Pages with a text layer are read directly. Pages that hold tables, charts or large figures are also rendered and sent to a vision model, which transcribes tables as markdown and describes charts by their axes and values. Scanned pages with no text layer go to OCR, which also takes over when the vision model fails.",
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
    body: "A question is checked, rewritten against the conversation, and searched through both indexes. If the passages found cannot answer it, a web search fills the gap. The answer cites each claim to a document page or web page, and a fact check drops any claim its source does not support.",
  },
];

const FACTS = [
  { term: "Formats", detail: "PDF, DOCX, JPG and PNG" },
  { term: "Upload limits", detail: "60 files per upload, 50 MB each" },
  { term: "Retrieval", detail: "Dense vectors and BM25, merged by rank fusion" },
  { term: "Evaluation", detail: "RAGAS, judged by a separate model family" },
];

export default function Home() {
  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 pb-24 pt-14 sm:px-6 sm:pt-20">
        <div className="max-w-3xl">
          <h1 className="text-balance font-serif text-4xl font-medium leading-[1.08] tracking-tight sm:text-5xl">
            Ask questions about documents whose answers sit in tables and charts
          </h1>

          <p className="mt-6 max-w-[62ch] text-base leading-relaxed text-ink-2">
            A retrieval-augmented generation system for documents whose content
            is not only text. Most of what a report actually says often sits in
            its tables and charts, where ordinary text extraction either drops
            it or flattens it into a row of numbers with no columns attached.
            This system reads that content with a vision model and keeps its
            structure, so a question whose answer lives in a table is still
            answerable.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
            <Link
              href="/chat"
              className="inline-flex items-center gap-2 rounded-md bg-ink px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-ink/85"
            >
              Open the chat
              <ArrowRight />
            </Link>
            <Link
              href="/evaluation"
              className="text-sm font-medium text-ink underline decoration-line-strong hover:decoration-ink"
            >
              See how it is evaluated
            </Link>
          </div>
        </div>

        <section className="mt-20 grid gap-x-12 gap-y-6 lg:grid-cols-[14rem_1fr]">
          <h2 className="font-serif text-2xl font-medium tracking-tight">
            How a document becomes an answer
          </h2>

          <ol className="border-t border-line">
            {PIPELINE.map((stage, index) => (
              <li
                key={stage.step}
                className="grid gap-x-8 gap-y-1 border-b border-line py-5 sm:grid-cols-[9rem_1fr]"
              >
                <h3 className="flex items-baseline gap-3 text-sm font-medium">
                  <span className="tnum font-mono text-xs text-muted">{index + 1}</span>
                  {stage.step}
                </h3>
                <p className="max-w-[68ch] text-sm leading-relaxed text-ink-2">{stage.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-16 grid gap-x-12 gap-y-6 lg:grid-cols-[14rem_1fr]">
          <h2 className="font-serif text-2xl font-medium tracking-tight">What works today</h2>

          <div>
            <p className="max-w-[68ch] text-sm leading-relaxed text-ink-2">
              The full pipeline runs end to end. Upload documents on the chat
              page and they are extracted, chunked and indexed; ask a question
              and it is answered from the corpus, with inline citations to the
              document and page. When the corpus cannot answer, the system
              falls back to a web search and cites the pages it used. Every
              answer is fact-checked against its sources, and the chat shows
              each pipeline stage as it runs plus the tokens and estimated cost
              it took. Ingestion is slow by nature, since every figure costs a
              vision call, so expect minutes rather than seconds for a large
              batch.
            </p>

            <dl className="mt-8 grid gap-x-10 border-t border-line sm:grid-cols-2">
              {FACTS.map((fact) => (
                <div key={fact.term} className="border-b border-line py-3">
                  <dt className="text-xs text-muted">{fact.term}</dt>
                  <dd className="mt-0.5 text-sm text-ink">{fact.detail}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </div>
    </main>
  );
}
