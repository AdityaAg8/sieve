"use client";

import { useReducedMotion } from "motion/react";

import type { Verdict } from "@/lib/api";

type Counts = Record<Verdict, number>;

type Props = {
  /** What the scanner flagged. The hero sentence's first number. */
  scannerFlagged: number | null;
  counts: Counts;
  /** Tools the worker said it would triage; the bar fills against this. */
  expected: number | null;
  streaming: boolean;
};

const SEGMENTS: { verdict: Verdict; label: string; fill: string }[] = [
  { verdict: "confirmed", label: "Confirmed", fill: "bg-red-fill" },
  { verdict: "needs_review", label: "Needs review", fill: "border-ink-0 border-y" },
  { verdict: "dismissed", label: "Dismissed", fill: "bg-ink-3" },
];

/**
 * The sieve, collapsed to one line: a grey "read" bar that splits into a red, a white-outlined
 * and a grey segment as verdicts arrive. Segments animate with `transform` only — each one is
 * full width and is translated and scaled into place — so nothing here reflows the page.
 */
export function VerdictBar({ scannerFlagged, counts, expected, streaming }: Props) {
  const reduceMotion = useReducedMotion() ?? false;
  const decided = counts.confirmed + counts.needs_review + counts.dismissed;
  const total = Math.max(expected ?? decided, decided, 1);

  // Cumulative offsets, built without mutating anything after render.
  const segments = SEGMENTS.reduce<
    { verdict: Verdict; label: string; fill: string; start: number; fraction: number; count: number }[]
  >((acc, segment) => {
    const previous = acc.at(-1);
    const start = previous ? previous.start + previous.fraction : 0;
    const count = counts[segment.verdict];
    return [...acc, { ...segment, start, fraction: count / total, count }];
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <p className="t-h3 text-ink-1">
        The scanner flagged{" "}
        <span className="t-num text-ink-0">{scannerFlagged ?? "—"}</span>. Sieve confirmed{" "}
        <span data-red="confirmed" className="t-num text-red-0">{counts.confirmed}</span>.
      </p>

      <div
        role="img"
        aria-label={
          `${decided} of ${expected ?? decided} tools triaged: ` +
          `${counts.confirmed} confirmed, ${counts.needs_review} need review, ` +
          `${counts.dismissed} dismissed.`
        }
        className="bg-bg-1 border-line-1 relative h-3 w-full overflow-hidden rounded-1 border"
      >
        {segments.map((segment) => (
          <span
            key={segment.verdict}
            data-red={segment.verdict === "confirmed" ? "confirmed" : undefined}
            aria-hidden="true"
            className={`absolute inset-y-0 left-0 w-full origin-left ${segment.fill}`}
            style={{
              transform: `translateX(${segment.start * 100}%) scaleX(${segment.fraction})`,
              transition: reduceMotion ? "none" : "transform var(--dur-3) var(--ease-out)",
            }}
          />
        ))}
      </div>

      <ul className="t-ui flex flex-wrap gap-x-6 gap-y-1">
        {segments.map((segment) => (
          <li key={segment.verdict} className="text-ink-2">
            {segment.label}{" "}
            <span
              data-testid={`verdict-bar-count-${segment.verdict}`}
              className="t-num text-ink-1"
            >
              {segment.count}
            </span>
          </li>
        ))}
        {streaming ? (
          <li aria-live="polite" className="text-ink-2 t-num">
            Triaging {decided} of {expected ?? "—"}…
          </li>
        ) : null}
      </ul>
    </div>
  );
}
