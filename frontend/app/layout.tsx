import type { Metadata } from "next";
import SiteHeader from "@/components/SiteHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: "Multimodal RAG",
  description: "Multimodal hybrid-retrieval RAG framework",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      {/* The shell owns the viewport height so the chat transcript scrolls in
          its own pane instead of the header scrolling away with it. */}
      <body className="flex h-screen flex-col overflow-hidden bg-white text-neutral-900 antialiased">
        <SiteHeader />
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </body>
    </html>
  );
}
