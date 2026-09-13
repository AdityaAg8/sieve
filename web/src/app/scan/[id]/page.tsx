import type { Metadata } from "next";

import { ResultsScreen } from "@/components/findings/ResultsScreen";
import { loadScanSnapshot } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Scan results — Sieve",
  description: "Confirmed findings first, with the scanner's verdict beside Sieve's.",
};

/**
 * A scan is read on every request: a worker is changing it while this page is being asked for.
 */
export const dynamic = "force-dynamic";

export default async function ScanResultsPage(props: PageProps<"/scan/[id]">) {
  const { id } = await props.params;
  const snapshot = await loadScanSnapshot(id);

  // The list is server-rendered when the server can read it. That is what closes this route's
  // cumulative layout shift: the first paint already has the rows, their groups and their
  // heights, and the event stream then updates rows that exist rather than creating them.
  return (
    <ResultsScreen
      scanId={id}
      initialScan={snapshot.state === "ok" ? snapshot.scan : null}
      initialFindings={snapshot.state === "ok" ? snapshot.findings : null}
      // A 404 here is an expired scan, and knowing it on the server means the reader never sees
      // a results page flash before the sentence that replaces it.
      initialMissing={snapshot.state === "missing"}
    />
  );
}
