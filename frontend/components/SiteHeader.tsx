"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import HealthBadge from "@/components/HealthBadge";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/chat", label: "Chat" },
  { href: "/evaluation", label: "Evaluation" },
];

export default function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="shrink-0 border-b border-line bg-surface">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-x-3 px-4 sm:gap-x-6 sm:px-6">
        <Link href="/" className="whitespace-nowrap font-serif text-[17px] font-medium tracking-tight">
          Multimodal RAG
        </Link>

        <nav className="flex h-full items-stretch gap-1" aria-label="Main">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex items-center px-1.5 text-[13px] transition-colors sm:px-2.5 sm:text-sm ${
                  active ? "font-medium text-ink" : "text-ink-2 hover:text-ink"
                }`}
              >
                {link.label}
                {active && <span className="absolute inset-x-1.5 -bottom-px h-0.5 bg-ink sm:inset-x-2.5" />}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto hidden sm:block">
          <HealthBadge />
        </div>
      </div>
    </header>
  );
}
