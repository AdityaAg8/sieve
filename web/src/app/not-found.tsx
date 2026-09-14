import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Not found — Sieve",
  description: "There is no page at this address.",
};

/**
 * The unknown route, in the same voice every empty state on this site uses: say what happened,
 * do not dress it as an error, and do not make the reader guess whether something is broken.
 *
 * The second sentence is the one that earns its place. A reader who lands here has almost always
 * followed a `/scan/{id}` link, and the likeliest reason is that the scan expired and was deleted
 * — which is Sieve working, not failing (PRD §13.3). Saying so here means the two pages tell one
 * story instead of two.
 */
export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-[84rem] flex-col gap-6 px-4 py-24 sm:px-8">
      <p className="t-eyebrow">Not found</p>
      <h1 className="t-h2 text-ink-0 max-w-[24ch]">There is no page at this address.</h1>

      <p className="t-body text-ink-1 max-w-[62ch]">
        Nothing failed and nothing was deleted here. If you followed a link to a scan, that scan
        may have expired instead — Sieve keeps a scan only until it expires, then deletes it with
        its tools and its verdicts.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <Link href="/scan" className="btn btn-primary">
          Start a new scan
        </Link>
        <Link href="/benchmark" className="btn btn-secondary">
          Read the benchmark
        </Link>
        <Link href="/" className="btn btn-quiet">
          Back to the start
        </Link>
      </div>
    </div>
  );
}
