import type { Verdict } from "@/lib/api";
import { VERDICT_LABEL } from "@/lib/verdict";

/**
 * Icon plus text, always. Colour never carries the verdict alone (PRD §14.6): the glyph
 * differs, the word differs, and the word is the accessible name.
 *
 * - Confirmed: a filled `--red-fill` square.
 * - Needs review: a hollow `--ink-0` ring.
 * - Dismissed: an `--ink-3` dash.
 */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const glyph = {
    confirmed: <span aria-hidden="true" className="bg-red-fill block h-2.5 w-2.5" />,
    needs_review: (
      <span aria-hidden="true" className="border-ink-0 block h-2.5 w-2.5 rounded-full border" />
    ),
    dismissed: <span aria-hidden="true" className="bg-ink-3 block h-px w-2.5" />,
  }[verdict];

  const tone = {
    confirmed: "text-ink-0 border-red-0",
    needs_review: "text-ink-0 border-line-2",
    dismissed: "text-ink-3 border-line-1",
  }[verdict];

  return (
    <span
      data-verdict={verdict}
      className={`t-ui inline-flex items-center gap-2 rounded-1 border px-2 py-1 ${tone}`}
    >
      {glyph}
      {VERDICT_LABEL[verdict]}
    </span>
  );
}
