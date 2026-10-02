import Link from "next/link";

/**
 * The mark: three grains above the sieve line, one below it. What falls through is red because
 * red means confirmed here — the glyph is the product's claim drawn once, not a decoration, and
 * it carries `data-red` for the same reason a confirmed chip does.
 *
 * `public/mark.svg` is the same glyph in one colour; every decorative appearance on the site is
 * a CSS mask through that file, never a redrawing.
 */
export function Mark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      className={className}
      data-red="confirmed"
    >
      <rect x="5" y="6" width="5" height="5" rx="1" fill="var(--ink-0)" />
      <rect x="13.5" y="6" width="5" height="5" rx="1" fill="var(--ink-0)" />
      <rect x="22" y="6" width="5" height="5" rx="1" fill="var(--ink-0)" />
      <rect x="2" y="15" width="28" height="2" rx="1" fill="var(--red-0)" />
      <rect x="13.5" y="21" width="5" height="5" rx="1" fill="var(--red-fill)" />
    </svg>
  );
}

/**
 * The lockup: mark, wordmark, and a tracked descriptor beneath the name. The descriptor drops
 * on a narrow screen rather than wrapping. The footer renders the same object smaller, not a
 * different asset.
 *
 * The nav lockup's accessible name is exactly "Sieve": the sub-label is decoration to a screen
 * reader and the keyboard suite asserts the name verbatim. The footer's copy of the same object
 * takes a different name so a page never carries two links called "Sieve".
 */
export function Logo({
  compact = false,
  label = "Sieve",
}: {
  compact?: boolean;
  /** The link's accessible name. */
  label?: string;
}) {
  return (
    <Link
      href="/"
      aria-label={label}
      className="inline-flex shrink-0 items-center gap-3 rounded-2 leading-none"
    >
      <Mark size={compact ? 24 : 28} />
      <span aria-hidden="true" className="flex flex-col gap-1">
        <span
          className={`font-display text-ink-0 font-extrabold tracking-[-0.02em] ${
            compact ? "text-[17px]" : "text-[20px]"
          }`}
        >
          Sieve
        </span>
        <span
          className={`text-ink-2 font-body text-[9px] font-semibold tracking-[0.2em] uppercase ${
            compact ? "" : "hidden sm:block"
          }`}
        >
          Triage, not detection
        </span>
      </span>
    </Link>
  );
}
