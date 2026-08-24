import type { Metadata } from "next";
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
      <body className="min-h-screen bg-white text-neutral-900 antialiased">
        {children}
      </body>
    </html>
  );
}