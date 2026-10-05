import type { Citation, QueryResponse } from "@/lib/api";

/** Answer text with its [n] markers turned into superscript links to the source list. */
export function AnswerText({ text, anchor }: { text: string; anchor: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="whitespace-pre-wrap">
      {parts.map((part, index) => {
        const marker = part.match(/^\[(\d+)\]$/);
        if (!marker) return part;
        return (
          <a
            key={index}
            href={`#${anchor}-${marker[1]}`}
            className="ml-0.5 align-super text-[10px] font-medium text-blue-700 hover:underline"
          >
            {marker[1]}
          </a>
        );
      })}
    </p>
  );
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function CitationItem({ citation }: { citation: Citation }) {
  if (citation.kind === "web") {
    return (
      <>
        <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-sky-800">
          Web
        </span>
        <a
          href={citation.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="truncate text-blue-700 hover:underline"
          title={citation.source_url}
        >
          {citation.title || hostname(citation.source_url)}
        </a>
        <span className="shrink-0 text-neutral-400">{hostname(citation.source_url)}</span>
      </>
    );
  }
  return (
    <>
      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-emerald-800">
        Doc
      </span>
      <span className="truncate text-neutral-700" title={citation.source_doc}>
        {citation.source_doc}
      </span>
      {citation.page !== null && (
        <span className="shrink-0 text-neutral-400">p. {citation.page}</span>
      )}
    </>
  );
}

export default function Citations({ citations, anchor }: { citations: Citation[]; anchor: string }) {
  if (citations.length === 0) return null;
  return (
    <ol className="mt-3 space-y-1.5 border-t border-neutral-200 pt-2">
      {citations.map((citation, index) => (
        <li key={index} id={`${anchor}-${index + 1}`} className="text-xs">
          <div className="flex min-w-0 items-center gap-2">
            <span className="w-4 shrink-0 tabular-nums text-neutral-400">{index + 1}</span>
            <CitationItem citation={citation} />
          </div>
          {citation.snippet && (
            <p className="ml-6 mt-0.5 line-clamp-2 text-neutral-500">{citation.snippet}</p>
          )}
        </li>
      ))}
    </ol>
  );
}

/** Cache, web and fact-check badges for a finished answer. */
export function AnswerBadges({ result }: { result: QueryResponse }) {
  const score = result.groundedness?.score ?? null;
  const usedWeb = result.citations.some((citation) => citation.kind === "web");
  const badges: { label: string; tone: string; title?: string }[] = [];

  if (result.cache_hit) badges.push({ label: "From cache", tone: "bg-neutral-200 text-neutral-700" });
  if (usedWeb) badges.push({ label: "Web fallback", tone: "bg-sky-100 text-sky-800" });
  if (score !== null) {
    const claims = result.groundedness?.claims ?? [];
    const supported = claims.filter((claim) => claim.supported).length;
    badges.push({
      label: `Fact check ${Math.round(score * 100)}%`,
      tone: score >= 0.8 ? "bg-emerald-100 text-emerald-800" : score >= 0.5 ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800",
      title: `${supported} of ${claims.length} claims supported by their sources`,
    });
  }
  if (result.removed_claims.length > 0) {
    badges.push({
      label: `${result.removed_claims.length} claim${result.removed_claims.length === 1 ? "" : "s"} removed`,
      tone: "bg-amber-100 text-amber-800",
      title: result.removed_claims.map((claim) => `${claim.reason}: ${claim.text}`).join("\n"),
    });
  }

  if (badges.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {badges.map((badge) => (
        <span
          key={badge.label}
          title={badge.title}
          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.tone}`}
        >
          {badge.label}
        </span>
      ))}
    </div>
  );
}

/** How the question was rewritten or split before answering. */
export function QuestionNotes({ result }: { result: QueryResponse }) {
  return (
    <>
      {result.resolved_question !== result.raw_question && (
        <p className="mb-2 text-xs text-neutral-500">
          Read as: <span className="italic">{result.resolved_question}</span>
        </p>
      )}
      {result.sub_questions && (
        <div className="mb-2 text-xs text-neutral-500">
          Answered in {result.sub_questions.length} parts:
          <ol className="ml-4 list-decimal">
            {result.sub_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}
