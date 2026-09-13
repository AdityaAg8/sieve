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
    <div className="mx-auto grid max-w-[84rem] gap-10 px-4 py-14 sm:px-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav aria-label="Docs" className="lg:sticky lg:top-6 lg:self-start">
        <ul className="flex flex-wrap gap-x-4 gap-y-2 lg:flex-col">
          {DOCS.map((doc) => (
            <li key={doc.slug}>
              <Link
                href={`/docs/${doc.slug}`}
                aria-current={doc.slug === slug ? "page" : undefined}
                className={`t-ui u-interactive border-b pb-0.5 ${
                  doc.slug === slug
                    ? "text-ink-0 border-ink-0"
                    : "text-ink-2 hover:text-ink-0 border-transparent"
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
