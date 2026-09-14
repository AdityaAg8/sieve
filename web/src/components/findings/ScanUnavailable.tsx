"use client";

import Link from "next/link";

/**
 * What `/scan/[id]` says when there is no scan to show.
 *
 * Two states, and they are not the same news. An **expired** scan is Sieve doing what it said it
 * would: a scan and everything in it is deleted when it expires (PRD §13.3), so a shared link
 * outlives the thing it points at and the page has to say so plainly rather than reading as an
 * error. A **failed** scan is the opposite — something went wrong and the reason is worth
 * printing, in the worker's own words.
 *
 * The date is rendered only when it is real. A purged scan answers 404 and a 404 carries no
 * `expires_at`; deriving one from a retention window this app never sees would be an invented
 * number (PRD §14.2), so the sentence drops the clause instead.
 */
type Props =
  | { state: "expired"; expiresAt?: string | null }
  | { state: "failed"; error: string | null; onRetry?: () => void };

/** "12 September 2026" — unambiguous across locales, which a shared link has to be. */
function onDay(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function ScanUnavailable(props: Props) {
  const expired = props.state === "expired";
  const day = expired && props.expiresAt ? onDay(props.expiresAt) : null;

  const heading = expired
    ? day
      ? `This scan expired on ${day}.`
      : "This scan expired."
    : `Scan failed: ${props.error ?? "no reason was recorded."}`;

  return (
    <div className="mx-auto flex max-w-[84rem] flex-col gap-6 px-4 py-24 sm:px-8">
      <p className="t-eyebrow">Scan</p>

      {/* The worker's error text came out of an analysed tool's neighbourhood and is untrusted
          data (PRD SC-7): it is a string child, never markup, never a URL. */}
      <h1 className="t-h2 text-ink-0 max-w-[24ch] break-words">{heading}</h1>

      <p className="t-body text-ink-1 max-w-[62ch]">
        {expired
          ? "Sieve keeps a scan only until it expires, then deletes it along with its tools and its verdicts. Nothing here is missing by accident, and nothing was found to be wrong."
          : "The worker stopped before it finished triaging this scan, so there are no verdicts to show. Nothing here says the tools are clean — it says Sieve could not tell you."}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <Link href="/scan" className="btn btn-primary">
          Start a new scan
        </Link>
        {!expired && props.onRetry ? (
          <button type="button" className="btn btn-secondary" onClick={props.onRetry}>
            Check this scan again
          </button>
        ) : null}
        <Link href="/" className="btn btn-quiet">
          Back to the start
        </Link>
      </div>
    </div>
  );
}
