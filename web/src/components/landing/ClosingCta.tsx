import Link from "next/link";

import { Reveal } from "@/components/shell/Reveal";

/** The page's last word: the same two actions the hero opened with, and the retention rule. */
export function ClosingCta() {
  return (
    <section aria-labelledby="cta-heading" className="band pt-0">
      <div className="wrap">
        <Reveal direction="scale" className="cta-banner">
          <div className="flex flex-col gap-3">
            <p className="eyebrow-rail">Your turn</p>
            <h2 id="cta-heading" className="t-h2 text-ink-0 max-w-[22ch]">
              Give Sieve your tools.
            </h2>
            <p className="t-body text-ink-2 max-w-[56ch]">
              Paste a tools/list dump or upload an mcp.json. Sieve parses it in your browser first
              and never starts a server. Scans are deleted after 7 days.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Link href="/scan" className="btn btn-primary">
              Start a scan
            </Link>
            <Link href="/docs/quickstart" className="btn btn-secondary">
              Read the quickstart
            </Link>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
