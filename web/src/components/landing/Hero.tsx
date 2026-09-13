"use client";

import Link from "next/link";

import { Sieve, type SieveDatum } from "@/components/sieve/Sieve";
import { demoScanId } from "@/lib/config";
import { useFindings, useScan } from "@/lib/queries";

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
    <section
      aria-labelledby="hero-heading"
      className="mx-auto grid max-w-[84rem] gap-12 px-4 pt-14 pb-20 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:items-center lg:gap-16"
    >
      <div className="flex flex-col items-start gap-7">
        <p className="t-eyebrow">Triage, not detection</p>

        {/*
          The sentence is always in the DOM and only the two numerals change, so the heading's
          box never resizes and the page's largest element causes no layout shift. A number that
          has not arrived reads `—`, the same way an unmeasured metric does.
        */}
        {/* Two blocks, one clause each: the two counts then sit one above the other, which is
            the whole comparison the sentence is making. */}
        <h1 id="hero-heading" className="t-display text-ink-1 max-w-[18ch]">
          <span className="block">
            The scanner flagged{" "}
            <span className="t-num text-ink-0">{scan.data ? scan.data.scanner_flagged : "—"}</span>.
          </span>{" "}
          <span className="block">
            Sieve confirmed{" "}
            <span className="t-num text-red-0">{scan.data ? scan.data.confirmed : "—"}</span>.
          </span>
        </h1>
        {scan.isPending ? (
          <p className="sr-only" aria-live="polite">
            Reading the sample scan…
          </p>
        ) : null}

        <p className="t-body text-ink-1 max-w-[52ch]">
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

        <p className="t-ui text-ink-2 max-w-[52ch]">
          Nothing is executed. Sieve parses the definitions you give it and never starts an MCP
          server.
        </p>
      </div>

      <div className="lg:pt-10">
        {findings.isPending || findings.isError ? (
          <SieveSkeleton />
        ) : (
          <Sieve data={tiles} autoplay replayable />
        )}
      </div>
    </section>
  );
}

/** A skeleton shaped like the sieve, not a spinner (PRD §14.7). */
function SieveSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-5">
      <div className="relative">
        <div className="h-14 sm:h-20" />
        <div className="bg-line-1 h-px w-full" />
      </div>
      {[0, 1, 2].map((tray) => (
        <div key={tray} className="flex flex-col gap-2">
          <div className="bg-bg-1 h-4 w-24 rounded-1" />
          <div className="border-line-1 flex h-[7.5rem] flex-wrap content-start gap-1.5 border-t pt-2">
            {[0, 1, 2].map((chip) => (
              <div key={chip} className="bg-bg-1 h-6 w-28 rounded-1" />
            ))}
          </div>
        </div>
      ))}
      <div className="border-line-1 h-12 border-t" />
    </div>
  );
}
