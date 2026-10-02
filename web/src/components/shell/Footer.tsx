import Link from "next/link";

import { footerDestinations, links } from "@/lib/links";

import { Logo } from "./Logo";

const PRODUCT = [
  { href: "/scan", label: "Scan" },
  { href: "/benchmark", label: "Benchmark" },
  { href: "/docs", label: "Docs" },
] as const;

/**
 * Docs pages the footer points at. Deliberately not the whole table of contents: the docs index
 * and the benchmark page already list every page by title, and a second link with the same name
 * on the same page is the kind of duplicate that confuses a screen-reader's link list.
 */
const USE_IT_FROM = [
  { href: "/docs/cli", label: "The CLI" },
  { href: "/docs/api", label: "The HTTP API" },
  { href: "/docs/action", label: "A GitHub Action" },
  { href: "/docs/mcp-server", label: "An MCP server" },
] as const;

/**
 * Four strata, no boxes: the brand column and three link columns under one hairline, then the
 * legal strip. The retention sentence is pinned verbatim by PRD §14.7 and lives in the strip.
 */
export function Footer() {
  return (
    <footer className="border-line-0 mt-24 border-t">
      <div className="wrap grid gap-10 py-12 md:grid-cols-[1.4fr_repeat(3,minmax(0,1fr))] md:gap-9">
        <div className="flex flex-col items-start gap-5">
          <Logo compact label="Sieve, back to the start" />
          <p className="t-ui text-ink-2 max-w-[34ch] font-normal">
            A triage layer for the findings an MCP tool-poisoning scanner emits. It reads what the
            scanner flagged and says what survives being read.
          </p>
        </div>

        <FooterColumn title="Product">
          {PRODUCT.map((item) => (
            <li key={item.href}>
              <Link href={item.href} className="footer-link">
                {item.label}
              </Link>
            </li>
          ))}
          {links.repo ? (
            <li>
              <a href={links.repo} rel="noreferrer noopener" target="_blank" className="footer-link">
                GitHub
              </a>
            </li>
          ) : null}
        </FooterColumn>

        <FooterColumn title="Use it from">
          {USE_IT_FROM.map((item) => (
            <li key={item.href}>
              <Link href={item.href} className="footer-link">
                {item.label}
              </Link>
            </li>
          ))}
        </FooterColumn>

        <FooterColumn title="Artefacts">
          {footerDestinations.map((destination) => (
            <li key={destination.label}>
              {destination.href ? (
                <a
                  href={destination.href}
                  rel="noreferrer noopener"
                  target="_blank"
                  className="footer-link"
                >
                  {destination.label}
                </a>
              ) : (
                <span className="t-ui text-ink-3 inline-flex min-h-6 items-center font-normal">
                  {destination.label}
                  <span className="text-ink-2">&nbsp;— not published yet</span>
                </span>
              )}
            </li>
          ))}
        </FooterColumn>
      </div>

      <div className="border-line-0 border-t">
        <div className="wrap flex flex-col gap-2 py-5 md:flex-row md:items-center md:justify-between">
          <p className="t-ui text-ink-2 font-normal">
            Scans are deleted after 7 days. Do not submit configurations containing secrets.
          </p>
          <p className="t-ui text-ink-3 font-normal">Sieve · triage, not detection</p>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="t-eyebrow">{title}</p>
      <ul className="flex flex-col gap-2">{children}</ul>
    </div>
  );
}
