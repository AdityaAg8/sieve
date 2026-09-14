"use client";

import { useEffect, useRef, useState } from "react";

import { NO_NUMBER, NO_NUMBER_LABEL } from "@/lib/bench";

/**
 * Every numeric cell on the benchmark page goes through here. A missing number renders as `—`,
 * never as 0 and never as a plausible stand-in, and carries "metric to confirm" as its title so
 * the reason is available on hover and to a screen reader.
 */
export function Num({ children }: { children: string }) {
  const missing = children === NO_NUMBER;
  return (
    <span
      className={`t-num ${missing ? "text-ink-3" : "text-ink-0"}`}
      title={missing ? NO_NUMBER_LABEL : undefined}
    >
      {children}
      {missing ? <span className="sr-only"> {NO_NUMBER_LABEL}</span> : null}
    </span>
  );
}

export function BenchSection({
  id,
  title,
  lede,
  children,
}: {
  id: string;
  title: string;
  lede?: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="border-line-1 flex min-w-0 flex-col gap-4 border-t pt-10">
      {/*
        `t-h3`, not `t-h2`: the page's own `h1` is `t-h2`, and eleven section headings set in the
        same face at the same size as the title left the page with no single thing at the top of
        its hierarchy. This is the shape `/scan` and `/docs` already use — title in the display
        face, section seams a step below it in the body face.
      */}
      <h2 id={id} className="t-h3 text-ink-0">
        {title}
      </h2>
      {lede ? <p className="t-body text-ink-1 max-w-[68ch]">{lede}</p> : null}
      {children}
    </section>
  );
}

/**
 * Tables scroll inside their own box; the page never scrolls sideways.
 *
 * The box is a tab stop **while it actually scrolls**. Below about 414 px every one of these
 * tables is wider than its box, and a scroll container with no focusable content is content a
 * keyboard cannot reach at all — the columns past the fold are unreadable without a pointer. At
 * 1280 px none of them overflow, and nine dead tab stops between the reader and the footer is a
 * different kind of harm, so the stop is taken away again when there is nothing to scroll.
 *
 * It starts present and is removed after measuring, never the other way round: the server render
 * and the first client paint both keep it, so a keyboard user on a narrow screen cannot lose the
 * table in the window before hydration.
 *
 * `role="group"` and the label are what stop the stop from being an unannounced one.
 */
export function TableWrap({ label, children }: { label: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(true);

  useEffect(() => {
    const box = ref.current;
    if (!box) return;
    const measure = () => setScrolls(box.scrollWidth > box.clientWidth);
    measure();

    // The box's width changes with the viewport; the table's changes when the benchmark's rows
    // arrive. Either can start or stop the overflow, so both are observed.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    if (box.firstElementChild) observer.observe(box.firstElementChild);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className="scroll-x border-line-1 rounded-2 border"
      tabIndex={scrolls ? 0 : -1}
      role="group"
      aria-label={label}
    >
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  );
}

export function Th({ children, scope = "col" }: { children: React.ReactNode; scope?: "col" | "row" }) {
  return (
    <th
      scope={scope}
      className="t-eyebrow border-line-1 bg-bg-1 border-b px-3 py-2 whitespace-nowrap"
    >
      {children}
    </th>
  );
}

export function Td({ children }: { children: React.ReactNode }) {
  return <td className="t-ui border-line-1 text-ink-1 border-b px-3 py-2 align-top">{children}</td>;
}

export function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="t-ui text-ink-2 border-line-1 border-l-2 pl-3">{children}</p>;
}
