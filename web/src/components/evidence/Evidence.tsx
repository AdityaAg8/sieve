"use client";

import { motion, useReducedMotion } from "motion/react";
import { useMemo } from "react";

import type { EvidenceSpan } from "@/lib/api";
import { mergeSpans, unlocatedQuotes } from "@/lib/evidence";

type Props = {
  text: string;
  spans: EvidenceSpan[];
  field: EvidenceSpan["field"];
  path?: string | null;
};

/** PRD §14.4: one 1 px `--red-0` line crosses the definition once, then stops. */
const SWEEP_MS = 600;

/**
 * The tool's own text, with the spans Sieve relied on marked in place.
 *
 * Everything rendered here is untrusted (PRD SC-7). It is inserted as text nodes only — there is
 * no `dangerouslySetInnerHTML` anywhere in this component — so a description containing markup
 * shows the markup rather than becoming it.
 */
export function Evidence({ text, spans, field, path = null }: Props) {
  const reduceMotion = useReducedMotion() ?? false;

  const mine = useMemo(
    () => spans.filter((span) => span.field === field && (span.path ?? null) === (path ?? null)),
    [spans, field, path],
  );

  const merged = useMemo(() => mergeSpans(mine, text.length), [mine, text.length]);
  const unlocated = useMemo(() => unlocatedQuotes(mine), [mine]);

  const pieces = merged.reduce<{ cursor: number; nodes: React.ReactNode[] }>(
    (acc, span, index) => {
      const before = text.slice(acc.cursor, span.start);
      const quote = text.slice(span.start, span.end);
      return {
        cursor: span.end,
        nodes: [
          ...acc.nodes,
          before ? <span key={`t${index}`}>{before}</span> : null,
          <mark
            key={`m${index}`}
            aria-label={`Evidence: ${span.quotes[0] ?? quote}`}
            data-red="evidence"
            className="evidence-mark"
          >
            {quote}
          </mark>,
        ],
      };
    },
    { cursor: 0, nodes: [] },
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="quote relative overflow-hidden p-3">
        {pieces.nodes}
        {text.slice(pieces.cursor)}

        {!reduceMotion && merged.length > 0 ? (
          // A full-width element whose only paint is its 1 px left border, translated across
          // once. Percentages then resolve against the container, so the line travels the whole
          // width with transform alone.
          <motion.span
            data-testid="evidence-sweep"
            data-red="line"
            aria-hidden="true"
            className="border-red-0 pointer-events-none absolute inset-y-0 left-0 w-full border-l"
            initial={{ x: 0, opacity: 0.9 }}
            animate={{ x: "100%", opacity: 0 }}
            transition={{ duration: SWEEP_MS / 1000, ease: [0.22, 1, 0.36, 1] }}
          />
        ) : null}
      </div>

      {unlocated.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="t-eyebrow">Quoted by the judge but not found verbatim</p>
          <ul className="flex flex-col gap-1">
            {unlocated.map((quote) => (
              <li key={quote} className="t-mono text-ink-2 border-line-1 border-l-2 pl-3">
                {quote}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
