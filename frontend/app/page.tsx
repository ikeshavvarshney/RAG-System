import Link from "next/link";
import { ArrowRight } from "@/components/Icons";
import SystemSnapshot from "@/components/SystemSnapshot";

const PIPELINE = [
  {
    step: "Extract",
    body: "Files are routed by their magic bytes. Text layers are read directly; pages with tables, charts or large figures are also rendered for a vision model, which transcribes tables as markdown and describes charts by axes and values. Scanned pages go to OCR, which also covers vision failures.",
  },
  {
    step: "Chunk",
    body: "Passages split at headings and paragraph breaks, targeting 500 tokens. Tables are never split. Each passage keeps its document, page and nearest heading.",
  },
  {
    step: "Index",
    body: "Every passage is embedded for dense retrieval and added to a BM25 keyword index: semantic search for paraphrase, keyword search for exact identifiers and names.",
  },
  {
    step: "Answer",
    body: "The question is checked, rewritten against the conversation and searched through both indexes, merged by rank fusion. If the passages cannot answer it, a web search fills the gap. Each claim is cited and fact-checked against its source.",
  },
];

const FACTS = [
  { term: "Formats", detail: "PDF, DOCX, JPG, PNG" },
  { term: "Upload limits", detail: "60 files per upload, 50 MB each" },
  { term: "Retrieval", detail: "Dense vectors + BM25, Reciprocal Rank Fusion" },
  { term: "Generation", detail: "Gemini, with per-claim fact check" },
  { term: "Fallback", detail: "Web search when the corpus cannot answer" },
  { term: "Evaluation", detail: "RAGAS, judged by a separate model family" },
];

export default function Home() {
  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 pb-20 pt-12 sm:px-6 sm:pt-16">
        <div className="grid gap-x-12 gap-y-10 lg:grid-cols-[1fr_22rem] lg:items-start">
          <div>
            <h1 className="max-w-[22ch] text-balance text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
              Question answering over documents whose answers sit in tables and charts
            </h1>
            <p className="mt-5 max-w-[62ch] text-[15px] leading-relaxed text-ink-2">
              Ordinary text extraction drops a report&apos;s tables and charts or flattens them into
              numbers with no columns attached. This system reads them with a vision model, indexes
              every passage for both semantic and keyword search, and answers with citations you can
              open to the exact page.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Link
                href="/chat"
                className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong"
              >
                Open the chat
                <ArrowRight width="15" height="15" />
              </Link>
              <Link
                href="/evaluation"
                className="inline-flex items-center gap-2 rounded-md border border-line-strong bg-raised px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-sunken"
              >
                View evaluation results
              </Link>
            </div>
          </div>

          <SystemSnapshot />
        </div>

        <section className="mt-16">
          <h2 className="text-lg font-semibold tracking-tight">How a document becomes an answer</h2>
          <ol className="mt-4 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE.map((stage, index) => (
              <li key={stage.step} className="bg-raised p-5">
                <h3 className="flex items-center gap-2.5 text-sm font-semibold">
                  <span className="tnum flex size-5 items-center justify-center rounded bg-accent-soft font-mono text-[11px] text-accent-strong">
                    {index + 1}
                  </span>
                  {stage.step}
                </h3>
                <p className="mt-2.5 text-[13px] leading-relaxed text-ink-2">{stage.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-14 grid gap-x-12 gap-y-6 lg:grid-cols-[1fr_1fr]">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">What to try</h2>
            <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-ink-2">
              <li>
                <span className="font-medium text-ink">Ask a table or chart question.</span> Pick an
                example on the chat page, then click a citation number to see the page with the
                passage highlighted.
              </li>
              <li>
                <span className="font-medium text-ink">Upload your own file.</span> Send it to this
                session to keep it private and out of the shared corpus.
              </li>
              <li>
                <span className="font-medium text-ink">Ask something the corpus cannot answer.</span>{" "}
                The answer falls back to the web and cites those pages instead.
              </li>
              <li>
                <span className="font-medium text-ink">Compare evaluation runs.</span> The evaluation
                page compares configurations over the questions both answered from the corpus.
              </li>
            </ul>
          </div>

          <div>
            <h2 className="text-lg font-semibold tracking-tight">Specification</h2>
            <dl className="mt-3 overflow-hidden rounded-lg border border-line bg-raised">
              {FACTS.map((fact) => (
                <div key={fact.term} className="grid grid-cols-[8rem_1fr] gap-3 border-b border-line px-4 py-2.5 text-sm last:border-b-0">
                  <dt className="text-muted">{fact.term}</dt>
                  <dd className="text-ink">{fact.detail}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </div>
    </main>
  );
}
