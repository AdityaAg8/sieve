"use client";

import Link from "next/link";

import { demoScanId } from "@/lib/config";
import { useScan } from "@/lib/queries";

/**
 * The sample is a pre-computed scan (PRD §13.4 seeds it), so "load the sample" is a link to a
 * result, not a new job. Saying so keeps the demo honest about what it is — and so does reading
 * its size from the scan rather than typing a number into the sentence.
 */
export function SampleLoader() {
  const scanId = demoScanId();
  const scan = useScan(scanId);
  const toolCount = scan.data?.tool_count ?? null;

  return (
    <aside className="surface flex flex-col gap-3 p-5">
      <h2 className="t-h3 text-ink-0">No tools to hand?</h2>
      <p className="t-body text-ink-1 max-w-[52ch]">
        The sample is{" "}
        <span data-testid="sample-tool-count" className="t-num">
          {toolCount ?? "—"}
        </span>{" "}
        tools from public MCP servers, triaged in advance and stored with the scanner&rsquo;s own
        findings beside Sieve&rsquo;s. Opening it starts no new work.
      </p>
      <div>
        <Link href={`/scan/${scanId}`} className="btn btn-secondary">
          Open the sample scan
        </Link>
      </div>
    </aside>
  );
}
