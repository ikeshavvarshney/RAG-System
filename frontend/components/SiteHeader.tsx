"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import HealthBadge from "@/components/HealthBadge";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/chat", label: "Chat" },
  { href: "/evaluation", label: "Evaluation" },
];

function Mark() {
  return (
    <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#101318" />
      <path d="M9 8h9l5 5v11H9z" fill="none" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
      <path d="M12.5 17h7M12.5 20.5h5" stroke="#8fb2f5" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export default function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="shrink-0 border-b border-line bg-raised">
      <div className="flex h-12 items-center gap-x-4 px-3 sm:gap-x-6 sm:px-4">
        <Link href="/" className="flex items-center gap-2 whitespace-nowrap text-sm font-semibold tracking-tight">
          <Mark />
          <span className="hidden sm:inline">Multimodal RAG</span>
        </Link>

        <nav className="flex h-full items-stretch" aria-label="Main">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex items-center px-2.5 text-[13px] transition-colors ${
                  active ? "font-medium text-ink" : "text-muted hover:text-ink"
                }`}
              >
                {link.label}
                {active && <span className="absolute inset-x-2.5 -bottom-px h-0.5 rounded-full bg-ink" />}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto">
          <HealthBadge />
        </div>
      </div>
    </header>
  );
}
