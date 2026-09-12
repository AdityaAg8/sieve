"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { links } from "@/lib/links";

const ROUTES = [
  { href: "/scan", label: "Scan" },
  { href: "/benchmark", label: "Benchmark" },
  { href: "/docs", label: "Docs" },
] as const;

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="border-b border-line-1">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-[84rem] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4 sm:px-8"
      >
        <Link
          href="/"
          className="font-display text-ink-0 text-[19px] font-semibold tracking-[-0.01em]"
        >
          Sieve
        </Link>

        <ul className="t-ui flex flex-1 items-center gap-5 sm:justify-end">
          {ROUTES.map((route) => {
            const active = pathname === route.href || pathname.startsWith(`${route.href}/`);
            return (
              <li key={route.href}>
                <Link
                  href={route.href}
                  aria-current={active ? "page" : undefined}
                  className={`u-interactive border-b pb-0.5 ${
                    active
                      ? "text-ink-0 border-red-0"
                      : "text-ink-2 hover:text-ink-0 border-transparent"
                  }`}
                >
                  {route.label}
                </Link>
              </li>
            );
          })}
          {links.repo ? (
            <li>
              <a
                href={links.repo}
                rel="noreferrer noopener"
                target="_blank"
                className="u-interactive text-ink-2 hover:text-ink-0 border-b border-transparent pb-0.5"
              >
                GitHub
              </a>
            </li>
          ) : null}
        </ul>
      </nav>
    </header>
  );
}
