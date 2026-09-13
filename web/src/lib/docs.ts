/**
 * The docs table of contents.
 *
 * The slug list is a literal rather than a directory read: `[slug]` is user-controlled input, and
 * an allow-list means a crafted slug can never reach the filesystem or the module loader. A page
 * is added here and as a file; nothing is discovered at runtime.
 */
export type Doc = { slug: string; title: string; blurb: string };

export const DOCS: Doc[] = [
  {
    slug: "quickstart",
    title: "Quickstart",
    blurb: "Triage your first tools/list dump in about a minute, with or without an API key.",
  },
  {
    slug: "cli",
    title: "CLI",
    blurb: "Run Sieve from a terminal, including fully offline in an air-gapped pipeline.",
  },
  {
    slug: "api",
    title: "API",
    blurb: "The HTTP contract: scans, the event stream, findings, SARIF, and feedback.",
  },
  {
    slug: "action",
    title: "GitHub Action",
    blurb: "Fail a build on confirmed findings only, and upload SARIF to code scanning.",
  },
  {
    slug: "mcp-server",
    title: "MCP server",
    blurb: "Let your own agent ask Sieve about a server before it trusts one.",
  },
  {
    slug: "limitations",
    title: "Limitations",
    blurb: "What Sieve does not do, where it is weakest, and what would change that.",
  },
];

export function isDocSlug(value: string): value is Doc["slug"] {
  return DOCS.some((doc) => doc.slug === value);
}

export function docFor(slug: string): Doc | undefined {
  return DOCS.find((doc) => doc.slug === slug);
}
