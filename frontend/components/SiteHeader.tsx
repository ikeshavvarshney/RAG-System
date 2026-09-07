"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import HealthBadge from "@/components/HealthBadge";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/chat", label: "Chat" },
];

export default function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="shrink-0 border-b border-neutral-200 bg-white">
      <div className="mx-auto flex max-w-4xl items-center gap-6 px-4 py-3">
        <Link href="/" className="text-sm font-semibold tracking-tight">
          Multimodal RAG
        </Link>

        <nav className="flex items-center gap-4">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={
                  active
                    ? "text-sm text-neutral-900"
                    : "text-sm text-neutral-500 hover:text-neutral-900"
                }
              >
                {link.label}
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
