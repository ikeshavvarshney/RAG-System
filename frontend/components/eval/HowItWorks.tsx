import { categoryLabel, type ReferenceSummary } from "@/lib/eval";

const STEPS = [
  {
    title: "Start from questions with known answers",
    body: "The reference set is written by hand from the source documents, never from system output. Each entry has a question, a reference answer, the documents and pages that contain it, and a category.",
  },
  {
    title: "Answer each one through the live pipeline",
    body: "Every question runs through the same pipeline the chat uses, with the answer cache bypassed so a previous configuration's answer cannot be returned. The answer and the passages retrieved for it are saved before any scoring, so a stopped run resumes where it left off.",
  },
  {
    title: "Record where each answer came from",
    body: "Web fallback stays on, since turning it off would measure a different system. Each answer is labelled corpus, web, both, or none. Comparisons between configurations use only the corpus-answered questions.",
  },
  {
    title: "Have a different model judge the result",
    body: "RAGAS asks a judge model to score four metrics per question, about twelve judge calls each. The judge is a Groq-hosted model, not Gemini, because a model grading its own answers tends to favour them.",
  },
  {
    title: "Compare runs, not single numbers",
    body: "One run says how the system does. The research questions need two: the same questions under a different fusion weight, or against the naive baseline. Pick a second run above to see the differences.",
  },
];

export default function HowItWorks({ reference }: { reference: ReferenceSummary | null }) {
  const categories = reference ? Object.entries(reference.per_category) : [];
  const largest = Math.max(1, ...categories.map(([, count]) => count));

  return (
    <div className="grid gap-x-12 gap-y-10 lg:grid-cols-[1fr_20rem]">
      <ol className="border-t border-line">
        {STEPS.map((step, index) => (
          <li key={step.title} className="grid grid-cols-[1.5rem_1fr] gap-x-3 border-b border-line py-4">
            <span className="tnum pt-0.5 font-mono text-xs text-muted">{index + 1}</span>
            <div>
              <h3 className="text-sm font-medium">{step.title}</h3>
              <p className="mt-1 max-w-[68ch] text-sm leading-relaxed text-ink-2">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>

      <div>
        <h3 className="text-sm font-medium">The reference set</h3>
        {reference ? (
          <>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              {reference.questions} questions, {reference.visual_dependent} of which can only be
              answered from a table or chart.
            </p>
            <ul className="mt-4 space-y-2.5">
              {categories.map(([category, count]) => (
                <li key={category}>
                  <div className="flex items-baseline justify-between text-xs">
                    <span className="text-ink-2">{categoryLabel(category)}</span>
                    <span className="tnum font-mono text-ink">{count}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-sm bg-sunken">
                    <div
                      className="h-full rounded-r-sm bg-accent"
                      style={{ width: `${(count / largest) * 100}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-xs leading-relaxed text-muted">
              The mix is deliberate. Keyword-heavy questions give sparse retrieval a chance to
              matter, and table and chart questions are the whole basis for comparing vision
              extraction with OCR. Out-of-corpus questions test for an honest non-answer and are
              not scored.
            </p>
          </>
        ) : (
          <p className="mt-1 text-xs text-muted">The reference set could not be loaded.</p>
        )}
      </div>
    </div>
  );
}
