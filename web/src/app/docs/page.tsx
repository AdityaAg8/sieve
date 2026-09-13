import type { Metadata } from "next";
import Link from "next/link";

import { DOCS } from "@/lib/docs";

export const metadata: Metadata = {
  title: "Docs — Sieve",
  description: "Quickstart, CLI, API, GitHub Action, MCP server, and limitations.",
};

export default function DocsIndexPage() {
  return (
    <div className="mx-auto flex max-w-[84rem] flex-col gap-8 px-4 py-14 sm:px-8">
      <header className="flex flex-col gap-3">
        <p className="t-eyebrow">Docs</p>
        <h1 className="t-h2 text-ink-0">Using Sieve</h1>
        <p className="t-body text-ink-1 max-w-[68ch]">
          Six pages. The last one is the list of things Sieve does not do, which is the page worth
          reading before the others.
        </p>
      </header>

      <ul className="grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
        {DOCS.map((doc) => (
          <li key={doc.slug} className="border-line-1 border-t pt-4">
            <Link
              href={`/docs/${doc.slug}`}
              className="t-h3 u-interactive text-ink-0 border-b border-transparent hover:border-ink-0"
            >
              {doc.title}
            </Link>
            <p className="t-ui text-ink-2 mt-2 max-w-[46ch]">{doc.blurb}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
