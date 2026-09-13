"use client";

import { useCallback, useMemo } from "react";

import { ApiError, type Finding, type ScanSummary, type Verdict } from "@/lib/api";
import { useFindings, useScan } from "@/lib/queries";
import { useScanEvents, type StreamStatus } from "@/lib/sse";

import { verdictCounts, type VerdictCounts } from "./FindingList";

export type ScanFeed = {
  scan: ScanSummary | null;
  /** The status the scan read failed with. `404` is an expired scan; `null` is no failure. */
  scanStatus: number | null;
  /** Every finding that has arrived, by whichever route was available. */
  findings: Finding[];
  counts: VerdictCounts;
  /** Rows each group holds from first paint, or `null` while that is not yet knowable. */
  reserved: VerdictCounts | null;
  loading: boolean;
  /** Both ways of reading the verdicts are gone. An outage, never a clean scan. */
  verdictsUnreadable: boolean;
  /** The event stream's own state, which is what the live region announces. */
  status: StreamStatus;
  /** Tools this scan says it will triage. */
  expected: number | null;
  /** What the worker said went wrong, from the scan row or from `scan.failed`. */
  error: string | null;
  retry: () => void;
};

type Options = {
  /** What the server renderer already read, so the first paint is the real list. */
  initialScan?: ScanSummary | null;
  initialFindings?: Finding[] | null;
  /** The verdict the page is filtered to, which narrows what there is to reserve. */
  filter?: Verdict | null;
};

/**
 * Where a results page's data comes from, in one place.
 *
 * There are three sources and they are not interchangeable: the server render (already in hand),
 * the event stream (the live one), and the paged endpoint (the fallback for a deployment where
 * SSE cannot connect — a proxy that buffers, a browser without `EventSource`). Choosing between
 * them, and working out how much room the list has to reserve, is the whole of this hook; the
 * results screen is then free to be about the screen.
 */
export function useScanFeed(scanId: string, options: Options = {}): ScanFeed {
  const { initialScan = null, initialFindings = null, filter = null } = options;

  const initialPage = useMemo(
    () =>
      initialFindings
        ? { items: initialFindings, total: initialFindings.length, next_cursor: null }
        : undefined,
    [initialFindings],
  );

  const scan = useScan(scanId, { initialData: initialScan ?? undefined });
  const live = useScanEvents(scanId);

  const streamFailed = live.status === "failed";
  const fallback = useFindings(scanId, undefined, {
    enabled: streamFailed,
    initialData: initialPage,
  });

  /**
   * The server render is the floor, not a stage the stream replaces: a scan that is already
   * complete has every finding in `initialFindings` and an empty stream, and a live one has a
   * partial server render that the stream then overtakes. Whichever list is longer is the one
   * with more of the scan in it.
   */
  const findings = useMemo<Finding[]>(() => {
    const streamed = streamFailed ? (fallback.data?.items ?? []) : live.findings;
    const served = initialFindings ?? [];
    return streamed.length >= served.length ? streamed : served;
  }, [streamFailed, fallback.data, live.findings, initialFindings]);

  const counts = useMemo(() => verdictCounts(findings), [findings]);

  const visible = useMemo(
    () => (filter ? findings.filter((f) => f.result.verdict === filter) : findings),
    [findings, filter],
  );

  /**
   * What the scan says its final split will be. Reserving those rows from first paint is what
   * keeps a live-filling list from shifting: a confirmed verdict that arrives after twenty
   * dismissed ones lands in a slot that already exists. Narrowed to the active filter, and
   * dropped entirely once the stream has finished.
   */
  const reserved = useMemo(() => {
    // Nothing left to reserve once the stream is done, or if the summary never arrived: the
    // reservation is exactly what is on screen, filter included.
    if (live.status === "complete" || streamFailed || scan.isError) return verdictCounts(visible);
    const summary = scan.data;
    // `null` means "not yet knowable": the list waits rather than rendering groups it will have
    // to re-order a moment later.
    if (!summary) return null;
    const all = {
      confirmed: summary.confirmed,
      needs_review: summary.needs_review,
      dismissed: summary.dismissed,
    };
    if (!filter) return all;
    return { confirmed: 0, needs_review: 0, dismissed: 0, [filter]: all[filter] };
  }, [scan.data, scan.isError, live.status, streamFailed, filter, visible]);

  const verdictsUnreadable = streamFailed && fallback.isError && findings.length === 0;

  const loading =
    !verdictsUnreadable &&
    (live.status === "connecting" ||
      live.status === "streaming" ||
      (streamFailed && fallback.isPending));

  const retry = useCallback(() => {
    void scan.refetch();
    void fallback.refetch();
  }, [scan, fallback]);

  return {
    scan: scan.data ?? null,
    scanStatus: scan.error instanceof ApiError ? scan.error.status : null,
    findings,
    counts,
    reserved,
    loading,
    verdictsUnreadable,
    status: live.status,
    expected: live.expected ?? scan.data?.tool_count ?? null,
    error: scan.data?.error ?? live.error ?? null,
    retry,
  };
}
