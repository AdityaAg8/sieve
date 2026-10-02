import type { Metadata } from "next";
import Link from "next/link";

import {
  IconArrow,
  IconFile,
  IconGate,
  IconScan,
  IconServer,
  IconSieve,
  IconTerminal,
} from "@/components/shell/Icons";
import { DOCS, type Doc } from "@/lib/docs";

export const metadata: Metadata = {
  title: "Docs — Sieve",
  description: "Quickstart, CLI, API, GitHub Action, MCP server, and limitations.",
};

const DOC_ICONS: Record<Doc["slug"], typeof IconSieve> = {
  quickstart: IconSieve,
  cli: IconTerminal,
  api: IconServer,
  action: IconGate,
  "mcp-server": IconScan,
  limitations: IconFile,
};

export default function DocsIndexPage() {
  return (
    <>
      <section className="band-hero pattern-gutters">
        <div className="wrap flex flex-col gap-3">
          <p className="eyebrow-rail with-mark">Docs</p>
          <h1 className="t-h2 text-ink-0">Using Sieve</h1>
          <p className="t-lead text-ink-1 max-w-[68ch]">
            Six pages. The last one is the list of things Sieve does not do, which is the page
            worth reading before the others.
          </p>
        </div>
      </section>

      <div className="wrap pb-20">
        <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {DOCS.map((doc) => {
            const Icon = DOC_ICONS[doc.slug];
            return (
              <li key={doc.slug} className="card card-lift">
                <span className="card-icon">
                  <Icon size={20} />
                </span>
                <Link
                  href={`/docs/${doc.slug}`}
                  className="t-h3 u-interactive text-ink-0 border-b border-transparent hover:border-ink-0 focus-visible:border-ink-0"
                >
                  {/* The card's only link, so its accessible name is unambiguous and the card
                      itself has exactly one destination. */}
                  {doc.title}
                </Link>
                <p className="t-body text-ink-1">{doc.blurb}</p>
                <span className="link-arrow mt-auto">
                  Read
                  <IconArrow size={16} />
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );
}
