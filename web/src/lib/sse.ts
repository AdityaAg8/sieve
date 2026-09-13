"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useApiReady } from "@/app/providers";

import { eventsUrl, type Finding, type ScanSummary } from "./api";
import { queryKeys } from "./queries";

export type StreamStatus = "connecting" | "streaming" | "complete" | "failed";

/**
 * What the worker actually puts on `scan.completed`: an id and the four counts it just wrote
 * (`sieve/worker/scan_job.py::_finish`). Deliberately *not* a `ScanSummary` — the authoritative
 * summary is refetched from `GET /scans/{id}`, and the counts here only stop the page from
 * showing stale ones for the length of that round trip.
 */
export type ScanCompletion = {
  scan_id?: string;
  tool_count?: number;
  confirmed?: number;
  needs_review?: number;
  dismissed?: number;
};

export type ScanEvents = {
  status: StreamStatus;
  /** Findings in arrival order. The stream does not group them; the list does. */
  findings: Finding[];
  summary: ScanCompletion | null;
  /** Tools the worker said it would triage, from `scan.started` or `scan.completed`. */
  expected: number | null;
  /**
   * The id of the last frame that arrived. The browser sends it back as `Last-Event-ID` on its
   * own reconnect, and the API replays only what came after it (`sieve/api/sse.py`).
   */
  lastEventId: string | null;
  /** What the worker said went wrong, from `scan.failed`. */
  error: string | null;
};

type StreamState = ScanEvents & { key: string };

/**
 * How many consecutive reconnects the browser may attempt before the page stops waiting and
 * falls back to the paged endpoint. The stream is the nice path, not the only one.
 */
const MAX_RETRIES = 5;

function fresh(key: string): StreamState {
  return {
    key,
    status: "connecting",
    findings: [],
    summary: null,
    expected: null,
    lastEventId: null,
    error: null,
  };
}

/**
 * Subscribes to `GET /scans/{id}/events` and lets the results page fill in as the worker decides
 * each tool. Every `tool.verdict` is also written into the TanStack cache, so a drawer opened
 * from a deep link finds the finding already there; `scan.completed` refetches the summary and
 * closes the stream.
 *
 * **Reconnects are the browser's.** `EventSource` retries on its own and sends the last `id:` it
 * saw as `Last-Event-ID`, which the API answers with only the events after it. The hook's part
 * is to survive that: a retry is not reported as a failure, and a replayed verdict is counted
 * once (the overlap is by design — the server resumes *after* an id the client may already
 * hold). Only a stream the browser has closed, or one that has burnt `MAX_RETRIES`, is a
 * failure worth falling back from.
 *
 * The whole stream lives in one state object stamped with the scan id. Switching scans is
 * therefore a *derived* reset rather than a `setState` in the effect body — which keeps the
 * cascading-render rule happy and, more usefully, means the hook can never render one scan's
 * findings under another scan's id.
 *
 * Everything in an event payload came from an analysed tool definition and is untrusted data
 * (PRD SC-7): it is parsed, stored and rendered as text — never evaluated, never as markup.
 */
export function useScanEvents(scanId: string): ScanEvents {
  const ready = useApiReady();
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState>(() => fresh(scanId));
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (!ready || scanId.length === 0) return;

    seen.current = new Set();
    let retries = 0;
    const update = (change: (previous: StreamState) => Partial<StreamState>) =>
      setState((previous) => {
        const base = previous.key === scanId ? previous : fresh(scanId);
        return { ...base, ...change(base) };
      });

    if (typeof EventSource === "undefined") {
      // The absence of EventSource is a fact about the environment; reported on the next tick so
      // the page can fall back to the paged endpoint.
      const timer = window.setTimeout(() => update(() => ({ status: "failed" })), 0);
      return () => window.clearTimeout(timer);
    }

    const source = new EventSource(eventsUrl(scanId));
    let closed = false;

    const parse = <T,>(event: Event): T | null => {
      try {
        return JSON.parse((event as MessageEvent).data) as T;
      } catch {
        return null;
      }
    };

    /** Every frame resets the retry budget and records the id a reconnect would resume from. */
    const received = (event: Event): Partial<StreamState> => {
      retries = 0;
      const id = (event as MessageEvent).lastEventId;
      return id ? { lastEventId: id } : {};
    };

    source.addEventListener("scan.started", (event) => {
      const payload = parse<{ tool_count?: number }>(event);
      update((previous) => ({
        ...received(event),
        status: "streaming",
        expected: typeof payload?.tool_count === "number" ? payload.tool_count : previous.expected,
      }));
    });

    source.addEventListener("tool.verdict", (event) => {
      const finding = parse<Finding>(event);
      if (!finding?.id || seen.current.has(finding.id)) {
        update(() => received(event));
        return;
      }
      seen.current.add(finding.id);
      update((previous) => ({
        ...received(event),
        status: "streaming",
        findings: [...previous.findings, finding],
      }));
      queryClient.setQueryData(queryKeys.findings(scanId), (cached: unknown) => {
        const page = cached as
          | { items: Finding[]; total: number; next_cursor: string | null }
          | undefined;
        const items = page ? [...page.items, finding] : [finding];
        return { items, total: items.length, next_cursor: null };
      });
    });

    source.addEventListener("scan.completed", (event) => {
      const payload = parse<ScanCompletion>(event) ?? {};
      // The event carries counts, not a summary. Merge them over whatever the page already has
      // and ask the API for the authoritative row; replacing the cache with this payload would
      // delete `expires_at`, `scanner_flagged` and `status` from a summary already on screen.
      queryClient.setQueryData(queryKeys.scan(scanId), (cached: unknown) =>
        cached ? { ...(cached as ScanSummary), ...payload, status: "complete" } : cached,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.scan(scanId) });
      update((previous) => ({
        ...received(event),
        status: "complete",
        summary: payload,
        expected: typeof payload.tool_count === "number" ? payload.tool_count : previous.expected,
      }));
      closed = true;
      source.close();
    });

    source.addEventListener("scan.failed", (event) => {
      const payload = parse<{ error?: string }>(event);
      void queryClient.invalidateQueries({ queryKey: queryKeys.scan(scanId) });
      update(() => ({
        ...received(event),
        status: "failed",
        error: typeof payload?.error === "string" ? payload.error : null,
      }));
      closed = true;
      source.close();
    });

    source.onerror = () => {
      if (closed) return; // a completed stream closes from our side
      // `CONNECTING` means the browser has already scheduled its own retry, and it will carry
      // `Last-Event-ID` with it. Anything else — or too many of these — is a real failure.
      retries += 1;
      if (source.readyState === EventSource.CONNECTING && retries <= MAX_RETRIES) return;
      closed = true;
      source.close();
      update((previous) => (previous.status === "complete" ? {} : { status: "failed" }));
    };

    return () => {
      closed = true;
      source.close();
    };
  }, [ready, scanId, queryClient]);

  return state.key === scanId ? state : fresh(scanId);
}
