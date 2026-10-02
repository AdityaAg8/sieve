import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DOCS, docFor, isDocSlug } from "@/lib/docs";

/** Every page is known at build time; `[slug]` is never used to reach the filesystem. */
export function generateStaticParams() {
  return DOCS.map((doc) => ({ slug: doc.slug }));
}

export const dynamicParams = false;

export async function generateMetadata(props: PageProps<"/docs/[slug]">): Promise<Metadata> {
  const { slug } = await props.params;
  const doc = docFor(slug);
  return doc
    ? { title: `${doc.title} — Sieve docs`, description: doc.blurb }
    : { title: "Docs — Sieve" };
}

const PAGES = {
  quickstart: () => import("../../../../content/docs/quickstart.mdx"),
  cli: () => import("../../../../content/docs/cli.mdx"),
  api: () => import("../../../../content/docs/api.mdx"),
  action: () => import("../../../../content/docs/action.mdx"),
  "mcp-server": () => import("../../../../content/docs/mcp-server.mdx"),
  limitations: () => import("../../../../content/docs/limitations.mdx"),
} as const;

export default async function DocPage(props: PageProps<"/docs/[slug]">) {
  const { slug } = await props.params;
  if (!isDocSlug(slug)) notFound();

  const { default: Content } = await PAGES[slug as keyof typeof PAGES]();

  return (
    <div className="wrap grid gap-10 py-10 pb-20 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav
        aria-label="Docs"
        className="panel lg:top-[calc(var(--nav-offset)+1rem)] p-4 lg:sticky lg:self-start"
      >
        <ul className="flex flex-col gap-1">
          {DOCS.map((doc) => (
            <li key={doc.slug}>
              <Link
                href={`/docs/${doc.slug}`}
                aria-current={doc.slug === slug ? "page" : undefined}
                className={`t-ui u-interactive rounded-2 block px-3 py-2 ${
                  doc.slug === slug
                    ? "text-ink-0 bg-bg-2 border-line-0 border"
                    : "text-ink-2 hover:text-ink-0 hover:bg-bg-2"
                }`}
              >
                {doc.title}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      <article className="min-w-0">
        <Content />
      </article>
    </div>
  );
}
