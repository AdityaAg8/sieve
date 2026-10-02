import type { Metadata } from "next";

import { SampleLoader } from "@/components/scan/SampleLoader";
import { ScanTabs } from "@/components/scan/ScanTabs";
import { IconCheck } from "@/components/shell/Icons";

export const metadata: Metadata = {
  title: "Scan — Sieve",
  description: "Give Sieve a tools/list dump, an mcp.json, or a remote MCP server to triage.",
};

export default function ScanPage() {
  return (
    <>
      <section className="band-hero pattern-gutters">
        <div className="wrap flex flex-col gap-3">
          <p className="eyebrow-rail with-mark">New scan</p>
          <h1 id="scan-heading" className="t-h2 text-ink-0">
            Give Sieve your tools
          </h1>
          <p className="t-lead text-ink-1 max-w-[58ch]">
            Sieve parses what you give it. It does not start your servers and it does not call your
            tools. Everything below is read in your browser first, so a malformed document is
            explained here rather than rejected later.
          </p>
        </div>
      </section>

      <section aria-labelledby="scan-heading">
        <div className="wrap grid gap-10 pb-20 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          <div className="panel p-5 sm:p-7">
            <ScanTabs />
          </div>

          <div className="flex flex-col gap-6">
            <SampleLoader />

            <ul className="checklist">
              <li>
                <span className="tick" aria-hidden="true">
                  <IconCheck size={13} />
                </span>
                Nothing is executed
              </li>
              <li>
                <span className="tick" aria-hidden="true">
                  <IconCheck size={13} />
                </span>
                No MCP server is started
              </li>
              <li>
                <span className="tick" aria-hidden="true">
                  <IconCheck size={13} />
                </span>
                Scans are deleted after 7 days
              </li>
            </ul>

            <p className="t-ui text-ink-2">
              Scans are deleted after 7 days. Do not submit configurations containing secrets.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
