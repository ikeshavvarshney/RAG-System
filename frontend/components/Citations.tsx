import type { ReactNode } from "react";
import { Doc, Globe } from "@/components/Icons";
import type { Citation, QueryResponse } from "@/lib/api";

function inline(text: string, anchor: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    const marker = part.match(/^\[(\d+)\]$/);
    if (marker) {
      return (
        <a
          key={key}
          href={`#${anchor}-${marker[1]}`}
          className="tnum mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded bg-accent-soft px-1 align-[1px] text-[11px] font-medium text-accent-strong no-underline hover:bg-accent-line"
        >
          {marker[1]}
        </a>
      );
    }
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    if (bold) return <strong key={key} className="font-semibold">{bold[1]}</strong>;
    return part;
  });
}

/** Answer text with [n] markers linked to the source list, plus bold and bullet lines. */
export function AnswerText({ text, anchor }: { text: string; anchor: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flush = () => {
    if (bullets.length === 0) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={`list-${blocks.length}`} className="list-disc space-y-1.5 pl-5 marker:text-line-strong">
        {items.map((item, index) => (
          <li key={index}>{inline(item, anchor, `li-${blocks.length}-${index}`)}</li>
        ))}
      </ul>,
    );
  };

  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flush();
    if (line.trim() === "") continue;
    blocks.push(<p key={`p-${blocks.length}`}>{inline(line, anchor, `p-${blocks.length}`)}</p>);
  }
  flush();

  return <div className="max-w-[70ch] space-y-3 text-[15px] leading-relaxed text-ink">{blocks}</div>;
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
        <Globe className="mt-0.5 shrink-0 text-series-web" />
        <div className="min-w-0">
          <a
            href={citation.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="block truncate font-medium text-ink underline decoration-line-strong hover:decoration-ink"
            title={citation.source_url}
          >
            {citation.title || hostname(citation.source_url)}
          </a>
          <p className="text-muted">Web, {hostname(citation.source_url)}</p>
          {citation.snippet && <p className="mt-1 line-clamp-2 text-ink-2">{citation.snippet}</p>}
        </div>
      </>
    );
  }
  return (
    <>
      <Doc className="mt-0.5 shrink-0 text-series-corpus" />
      <div className="min-w-0">
        <p className="truncate font-medium text-ink" title={citation.source_doc}>
          {citation.source_doc}
        </p>
        <p className="text-muted">
          Document{citation.page !== null && `, page ${citation.page}`}
        </p>
        {citation.snippet && <p className="mt-1 line-clamp-2 text-ink-2">{citation.snippet}</p>}
      </div>
    </>
  );
}

export default function Citations({ citations, anchor }: { citations: Citation[]; anchor: string }) {
  if (citations.length === 0) return null;
  return (
    <section className="mt-5">
      <h3 className="text-xs font-medium text-muted">
        {citations.length} source{citations.length === 1 ? "" : "s"}
      </h3>
      <ol className="mt-2 divide-y divide-line border-y border-line">
        {citations.map((citation, index) => (
          <li
            key={index}
            id={`${anchor}-${index + 1}`}
            className="flex scroll-mt-4 gap-2.5 py-2.5 text-xs target:bg-accent-soft"
          >
            <span className="tnum w-4 shrink-0 pt-0.5 text-right text-muted">{index + 1}</span>
            <CitationItem citation={citation} />
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Cache, web and fact-check badges for a finished answer. */
export function AnswerBadges({ result }: { result: QueryResponse }) {
  const score = result.groundedness?.score ?? null;
  const usedWeb = result.citations.some((citation) => citation.kind === "web");
  const badges: { label: string; tone: string; title?: string }[] = [];

  if (score !== null) {
    const claims = result.groundedness?.claims ?? [];
    const supported = claims.filter((claim) => claim.supported).length;
    badges.push({
      label: `Fact check ${Math.round(score * 100)}%`,
      tone:
        score >= 0.8
          ? "bg-good-soft text-good"
          : score >= 0.5
            ? "bg-warn-soft text-warn"
            : "bg-bad-soft text-bad",
      title: `${supported} of ${claims.length} claims supported by their sources`,
    });
  }
  if (usedWeb) badges.push({ label: "Web fallback", tone: "bg-sunken text-ink-2" });
  if (result.cache_hit) badges.push({ label: "From cache", tone: "bg-sunken text-ink-2" });
  if (result.removed_claims.length > 0) {
    badges.push({
      label: `${result.removed_claims.length} claim${result.removed_claims.length === 1 ? "" : "s"} removed`,
      tone: "bg-warn-soft text-warn",
      title: result.removed_claims.map((claim) => `${claim.reason}: ${claim.text}`).join("\n"),
    });
  }

  if (badges.length === 0) return null;
  return (
    <div className="mt-4 flex flex-wrap gap-1.5">
      {badges.map((badge) => (
        <span
          key={badge.label}
          title={badge.title}
          className={`rounded px-2 py-0.5 text-xs font-medium ${badge.tone}`}
        >
          {badge.label}
        </span>
      ))}
    </div>
  );
}

/** How the question was rewritten or split before answering. */
export function QuestionNotes({ result }: { result: QueryResponse }) {
  const rewritten = result.resolved_question !== result.raw_question;
  if (!rewritten && !result.sub_questions) return null;
  return (
    <div className="mb-3 space-y-1.5 border-l border-line-strong pl-3 text-xs text-ink-2">
      {rewritten && (
        <p>
          Read as: <span className="font-serif text-[13px] italic">{result.resolved_question}</span>
        </p>
      )}
      {result.sub_questions && (
        <div>
          Answered in {result.sub_questions.length} parts:
          <ol className="ml-4 mt-1 list-decimal space-y-0.5">
            {result.sub_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
