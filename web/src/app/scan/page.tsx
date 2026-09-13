import type { Metadata } from "next";

import { SampleLoader } from "@/components/scan/SampleLoader";
import { ScanTabs } from "@/components/scan/ScanTabs";

export const metadata: Metadata = {
  title: "Scan — Sieve",
  description: "Give Sieve a tools/list dump, an mcp.json, or a remote MCP server to triage.",
};

export default function ScanPage() {
  return (
    <div className="mx-auto grid max-w-[84rem] gap-12 px-4 py-14 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
      <section aria-labelledby="scan-heading" className="flex flex-col gap-8">
        <div className="flex flex-col gap-3">
          <p className="t-eyebrow">New scan</p>
          <h1 id="scan-heading" className="t-h2 text-ink-0">
            Give Sieve your tools
          </h1>
          <p className="t-body text-ink-1 max-w-[58ch]">
            Sieve parses what you give it. It does not start your servers and it does not call your
            tools. Everything below is read in your browser first, so a malformed document is
            explained here rather than rejected later.
          </p>
        </div>

        <ScanTabs />
      </section>

      <div className="flex flex-col gap-6 lg:pt-14">
        <SampleLoader />
        <p className="t-ui text-ink-2">
          Scans are deleted after 7 days. Do not submit configurations containing secrets.
        </p>
      </div>
    </div>
  );
}
