"use client";

import Link from "next/link";

import { ErrorNote } from "@/components/shell/ErrorNote";
import { IconClock, IconLock, IconServer } from "@/components/shell/Icons";
import { Sieve, type SieveDatum } from "@/components/sieve/Sieve";
import { demoScanId } from "@/lib/config";
import { useFindings, useScan } from "@/lib/queries";

/**
 * The thesis, then the proof. Left: the sentence with its two numerals, a lead, two actions and
 * three chrome-less trust chips. Right: the sieve, housed in the page's one glass slab — a
 * working demo on real fixture data rather than an illustration of one.
 */
export function Hero() {
  const scanId = demoScanId();
  const scan = useScan(scanId);
  const findings = useFindings(scanId);

  const tiles: SieveDatum[] = (findings.data?.items ?? []).map((finding) => ({
    id: finding.id,
    label: finding.id,
    verdict: finding.result.verdict,
    flaggedByScanner: finding.result.scanner_findings.length > 0,
  }));

  return (
    <section aria-labelledby="hero-heading" className="band-hero pattern-gutters bloom">
      <div className="wrap grid gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:items-center lg:gap-14">
        <div className="hero-anim flex flex-col items-start gap-7">
          <p className="eyebrow-rail with-mark">Triage, not detection</p>

          {/*
            The sentence is always in the DOM and only the two numerals change, so the heading's
            box never resizes and the page's largest element causes no layout shift. A number that
            has not arrived reads `—`, the same way an unmeasured metric does. Two blocks, one
            clause each: the two counts then sit one above the other, which is the whole
            comparison the sentence is making.
          */}
          <h1 id="hero-heading" className="t-display text-ink-1 max-w-[18ch]">
            <span className="block">
              The scanner flagged{" "}
              <span className="t-num text-ink-0">{scan.data ? scan.data.scanner_flagged : "—"}</span>.
            </span>{" "}
            <span className="block">
              Sieve confirmed{" "}
              <span data-red="confirmed" className="t-num text-red-0">
                {scan.data ? scan.data.confirmed : "—"}
              </span>
              .
            </span>
          </h1>
          {scan.isPending ? (
            <p className="sr-only" aria-live="polite">
              Reading the sample scan…
            </p>
          ) : null}

          <p className="t-lead text-ink-1 max-w-[52ch]">
            A scanner tells you what matched a pattern. Sieve reads every tool definition the way an
            analyst would, states which class it thinks each one is, and shows you the span it read.
            You keep the scanner&rsquo;s verdict beside ours, so you can disagree.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <Link href={`/scan/${scanId}`} className="btn btn-primary">
              Try the sample
            </Link>
            <Link href="/scan" className="btn btn-secondary">
              Paste your tools
            </Link>
          </div>

          <ul className="t-ui flex flex-wrap gap-x-6 gap-y-2">
            <li className="chip-trust">
              <IconLock size={18} />
              Nothing is executed
            </li>
            <li className="chip-trust">
              <IconServer size={18} />
              Never starts an MCP server
            </li>
            <li className="chip-trust">
              <IconClock size={18} />
              Scans deleted after 7 days
            </li>
          </ul>
        </div>

        <div className="hero-visual-anim panel p-5 sm:p-6">
          {findings.isError || scan.isError ? (
            <ErrorNote
              title="The sample scan could not be read."
              detail="The demo is served from a stored scan; it is not reachable right now."
              className="bg-transparent border-0 p-0"
              onRetry={() => {
                void scan.refetch();
                void findings.refetch();
              }}
            />
          ) : findings.isPending ? (
            <SieveSkeleton />
          ) : (
            <Sieve
              data={tiles}
              autoplay
              replayable
              caption={`Sample scan · ${scan.data?.tool_count ?? tiles.length} tools`}
            />
          )}
        </div>
      </div>
    </section>
  );
}

/** A skeleton shaped like the sieve, not a spinner (PRD §14.7). */
function SieveSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-5">
      <div className="panel-head">
        <div className="bg-bg-2 h-3 w-36 rounded-1" />
        <div className="bg-bg-2 h-3 w-28 rounded-1" />
      </div>
      <div className="relative">
        <div className="h-12 sm:h-16" />
        <div className="bg-line-1 h-px w-full" />
      </div>
      {[0, 1, 2].map((tray) => (
        <div key={tray} className="flex flex-col gap-2">
          <div className="bg-bg-2 h-3 w-24 rounded-1" />
          <div className="border-line-1 flex h-[6.5rem] flex-wrap content-start gap-1.5 border-t pt-2">
            {[0, 1, 2].map((chip) => (
              <div key={chip} className="bg-bg-2 h-6 w-28 rounded-1" />
            ))}
          </div>
        </div>
      ))}
      <div className="border-line-1 h-10 border-t" />
    </div>
  );
}
