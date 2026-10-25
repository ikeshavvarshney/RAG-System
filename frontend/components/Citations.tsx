"use client";

import type { ReactNode } from "react";
import { useActiveViewer, useOpenViewer } from "@/components/DocumentViewer";
import { Doc, External, Globe } from "@/components/Icons";
import type { Citation, QueryResponse } from "@/lib/api";
import type { ViewerTarget } from "@/lib/viewer";

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function citationTitle(citation: Citation): string {
  return citation.kind === "web"
    ? `${citation.title || hostname(citation.source_url)} (web)`
    : `${citation.source_doc}${citation.page !== null ? `, page ${citation.page}` : ""}`;
}

function viewerTarget(citation: Citation): ViewerTarget | null {
  return citation.kind === "corpus"
    ? { sourceDoc: citation.source_doc, page: citation.page, chunkId: citation.chunk_id }
    : null;
}

function isActive(citation: Citation, active: ViewerTarget | null): boolean {
  return (
    citation.kind === "corpus" &&
    active !== null &&
    active.sourceDoc === citation.source_doc &&
    active.chunkId === citation.chunk_id
  );
}

function Marker({
  number,
  citation,
  anchor,
  open,
}: {
  number: string;
  citation: Citation | undefined;
  anchor: string;
  open: (target: ViewerTarget) => void;
}) {
  const active = useActiveViewer();
  const selected = citation !== undefined && isActive(citation, active);
  const style = `tnum mx-0.5 inline-flex h-4.5 min-w-4.5 items-center justify-center rounded px-1 align-[1px] font-mono text-[11px] font-medium no-underline transition-colors hover:bg-accent hover:text-white ${
    selected ? "bg-accent text-white" : "bg-accent-soft text-accent-strong"
  }`;

  if (!citation) {
    return (
      <a href={`#${anchor}-${number}`} className={style}>
        {number}
      </a>
    );
  }
  if (citation.kind === "web") {
    return (
      <a href={citation.source_url} target="_blank" rel="noopener noreferrer" title={citationTitle(citation)} className={style}>
        {number}
      </a>
    );
  }
  return (
    <button type="button" onClick={() => open(viewerTarget(citation)!)} title={`Open ${citationTitle(citation)}`} className={style}>
      {number}
    </button>
  );
}

function inline(
  text: string,
  citations: Citation[],
  anchor: string,
  open: (target: ViewerTarget) => void,
  keyPrefix: string,
): ReactNode[] {
  return text.split(/(\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]|\*\*[^*]+\*\*)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (/^\[[\d\s,;]+\]$/.test(part)) {
      return (part.match(/\d+/g) ?? []).map((number) => (
        <Marker
          key={`${key}-${number}`}
          number={number}
          citation={citations[Number(number) - 1]}
          anchor={anchor}
          open={open}
        />
      ));
    }
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    if (bold) return <strong key={key} className="font-semibold">{bold[1]}</strong>;
    return part;
  });
}

/** Answer text with [n] markers that open their source, plus bold and bullet lines. */
export function AnswerText({ text, citations, anchor }: { text: string; citations: Citation[]; anchor: string }) {
  const open = useOpenViewer();
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flush = () => {
    if (bullets.length === 0) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={`list-${blocks.length}`} className="list-disc space-y-1.5 pl-5 marker:text-line-strong">
        {items.map((item, index) => (
          <li key={index}>{inline(item, citations, anchor, open, `li-${blocks.length}-${index}`)}</li>
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
    blocks.push(<p key={`p-${blocks.length}`}>{inline(line, citations, anchor, open, `p-${blocks.length}`)}</p>);
  }
  flush();

  return <div className="max-w-[70ch] space-y-3 text-[15px] leading-relaxed text-ink">{blocks}</div>;
}

function CitationRow({ citation, number, anchor }: { citation: Citation; number: number; anchor: string }) {
  const openViewer = useOpenViewer();
  const selected = isActive(citation, useActiveViewer());
  const isWeb = citation.kind === "web";
  const body = (
    <>
      <span
        className={`tnum flex size-5 shrink-0 items-center justify-center rounded font-mono text-[11px] ${
          selected ? "bg-accent text-white" : "bg-sunken text-ink-2 group-hover:bg-accent-soft group-hover:text-accent-strong"
        }`}
      >
        {number}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          {isWeb ? (
            <Globe className="shrink-0 text-series-web" width="14" height="14" />
          ) : (
            <Doc className="shrink-0 text-series-corpus" width="14" height="14" />
          )}
          <span className="truncate font-medium text-ink">
            {isWeb ? citation.title || hostname(citation.source_url) : citation.source_doc}
          </span>
          <span className="tnum shrink-0 text-muted">
            {isWeb ? hostname(citation.source_url) : citation.page !== null ? `p. ${citation.page}` : ""}
          </span>
          {isWeb && <External className="shrink-0 text-muted" width="12" height="12" />}
        </span>
        {citation.snippet && <span className="mt-0.5 line-clamp-2 block text-ink-2">{citation.snippet}</span>}
      </span>
    </>
  );

  const className = `group flex w-full items-start gap-2.5 px-3 py-2 text-left text-xs transition-colors ${
    selected ? "bg-accent-soft shadow-[inset_2px_0_0_var(--color-accent)]" : "hover:bg-surface"
  }`;

  return (
    <li id={`${anchor}-${number}`} className="scroll-mt-4 border-b border-line last:border-b-0 target:bg-accent-soft">
      {isWeb ? (
        <a href={citation.source_url} target="_blank" rel="noopener noreferrer" title={citation.source_url} className={className}>
          {body}
        </a>
      ) : (
        <button
          type="button"
          onClick={() => openViewer(viewerTarget(citation)!)}
          title={`Open ${citationTitle(citation)}`}
          aria-current={selected ? "true" : undefined}
          className={className}
        >
          {body}
        </button>
      )}
    </li>
  );
}

export default function Citations({ citations, anchor }: { citations: Citation[]; anchor: string }) {
  if (citations.length === 0) return null;
  return (
    <section className="mt-5">
      <h3 className="text-xs font-medium text-muted">
        Sources <span className="tnum">({citations.length})</span>
      </h3>
      <ol className="mt-1.5 overflow-hidden rounded-lg border border-line">
        {citations.map((citation, index) => (
          <CitationRow key={index} citation={citation} number={index + 1} anchor={anchor} />
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
      label: `Fact check ${Math.round(score * 100)}% · ${supported}/${claims.length} claims`,
      tone:
        score >= 0.8
          ? "bg-good-soft text-good"
          : score >= 0.5
            ? "bg-warn-soft text-warn"
            : "bg-bad-soft text-bad",
      title: `${supported} of ${claims.length} claims supported by their sources`,
    });
  }
  if (usedWeb) badges.push({ label: "Web fallback", tone: "bg-sunken text-ink-2", title: "The documents could not answer, so web pages were searched" });
  if (result.cache_hit) badges.push({ label: "From cache", tone: "bg-sunken text-ink-2", title: "Returned from the answer cache" });
  if (result.removed_claims.length > 0) {
    badges.push({
      label: `${result.removed_claims.length} unsupported claim${result.removed_claims.length === 1 ? "" : "s"} removed`,
      tone: "bg-warn-soft text-warn",
      title: result.removed_claims.map((claim) => `${claim.reason}: ${claim.text}`).join("\n"),
    });
  }

  if (badges.length === 0) return null;
  return (
    <div className="mt-4 flex flex-wrap gap-1.5">
      {badges.map((badge) => (
        <span key={badge.label} title={badge.title} className={`tnum rounded px-2 py-0.5 text-xs font-medium ${badge.tone}`}>
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
    <div className="mb-4 space-y-1.5 rounded-md bg-surface px-3 py-2 text-xs text-ink-2">
      {rewritten && (
        <p>
          <span className="font-medium text-ink">Read as</span> {result.resolved_question}
        </p>
      )}
      {result.sub_questions && (
        <div>
          <span className="font-medium text-ink">Answered in {result.sub_questions.length} parts</span>
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
