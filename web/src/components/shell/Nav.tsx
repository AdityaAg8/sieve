"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { links } from "@/lib/links";

import { IconClose, IconMenu } from "./Icons";
import { Logo } from "./Logo";

const ROUTES = [
  { href: "/scan", label: "Scan" },
  { href: "/benchmark", label: "Benchmark" },
  { href: "/docs", label: "Docs" },
] as const;

/**
 * The floating capsule. Fixed and translucent at every scroll position; on scroll it only
 * deepens its tint and its shadow, never its height, so the page under it never jumps. Below
 * `md` the destinations move into a sheet that drops from beneath the capsule.
 */
export function Nav() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  // The sheet is open *for a route*: navigating away closes it without an effect, because the
  // stored route no longer matches the current one.
  const [openFor, setOpenFor] = useState<string | null>(null);
  const open = openFor === pathname;
  const setOpen = (next: boolean) => setOpenFor(next ? pathname : null);
  const sheetId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Escape closes the sheet and hands focus back to the toggle.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        toggleRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // `setOpen` is a fresh closure each render; the effect only needs to know whether it is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <header className="capsule" data-scrolled={scrolled ? "true" : "false"}>
      <nav aria-label="Main" className="capsule-row">
        <Logo />

        <ul className="hidden items-center gap-1 md:flex">
          {ROUTES.map((route) => (
            <li key={route.href}>
              <Link
                href={route.href}
                aria-current={isActive(route.href) ? "page" : undefined}
                className="nav-link"
              >
                {route.label}
              </Link>
            </li>
          ))}
          {links.repo ? (
            <li>
              <a
                href={links.repo}
                rel="noreferrer noopener"
                target="_blank"
                className="nav-link"
              >
                GitHub
              </a>
            </li>
          ) : null}
        </ul>

        <div className="flex items-center gap-2">
          <Link href="/scan" className="btn btn-primary btn-sm hidden sm:inline-flex">
            Start a scan
          </Link>
          <button
            ref={toggleRef}
            type="button"
            aria-expanded={open}
            aria-controls={sheetId}
            aria-label={open ? "Close the menu" : "Open the menu"}
            onClick={() => setOpen(!open)}
            className="btn btn-quiet btn-sm text-ink-0 px-2 md:hidden"
          >
            {open ? <IconClose size={22} /> : <IconMenu size={22} />}
          </button>
        </div>
      </nav>

      {open ? (
        <div id={sheetId} className="nav-sheet md:hidden">
          {ROUTES.map((route) => (
            <Link
              key={route.href}
              href={route.href}
              aria-current={isActive(route.href) ? "page" : undefined}
              className="nav-link min-h-11"
            >
              {route.label}
            </Link>
          ))}
          {links.repo ? (
            <a
              href={links.repo}
              rel="noreferrer noopener"
              target="_blank"
              className="nav-link min-h-11"
            >
              GitHub
            </a>
          ) : null}
          <Link href="/scan" className="btn btn-primary mt-2 sm:hidden">
            Start a scan
          </Link>
        </div>
      ) : null}
    </header>
  );
}
