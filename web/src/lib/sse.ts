"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useApiReady } from "@/app/providers";

import { eventsUrl, type Finding, type ScanSummary } from "./api";
import { queryKeys } from "./queries";

export type StreamStatus = "connecting" | "streaming" | "complete" | "failed";

export type ScanEvents = {
  status: StreamStatus;
  /** Findings in arrival order. The stream does not group them; the list does. */
  findings: Finding[];
  summary: ScanSummary | null;
  /** Tools the worker said it would triage, from `scan.started`. */
  expected: number | null;
};

type StreamState = ScanEvents & { key: string };

function fresh(key: string): StreamState {
  return { key, status: "connecting", findings: [], summary: null, expected: null };
}

/**
 * Subscribes to `GET /scans/{id}/events` and lets the results page fill in as the worker decides
 * each tool. Every `tool.verdict` is also written into the TanStack cache, so a drawer opened
 * from a deep link finds the finding already there; `scan.completed` writes the summary and
 * closes the stream.
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

    source.addEventListener("scan.started", (event) => {
      const payload = parse<{ tool_count?: number }>(event);
      update((previous) => ({
        status: "streaming",
        expected: typeof payload?.tool_count === "number" ? payload.tool_count : previous.expected,
      }));
    });

    source.addEventListener("tool.verdict", (event) => {
      const finding = parse<Finding>(event);
      if (!finding?.id || seen.current.has(finding.id)) return;
      seen.current.add(finding.id);
      update((previous) => ({ status: "streaming", findings: [...previous.findings, finding] }));
      queryClient.setQueryData(queryKeys.findings(scanId), (cached: unknown) => {
        const page = cached as
          | { items: Finding[]; total: number; next_cursor: string | null }
          | undefined;
        const items = page ? [...page.items, finding] : [finding];
        return { items, total: items.length, next_cursor: null };
      });
    });

    source.addEventListener("scan.completed", (event) => {
      const payload = parse<ScanSummary>(event);
      if (payload) queryClient.setQueryData(queryKeys.scan(scanId), payload);
      update(() => ({ status: "complete", summary: payload }));
      closed = true;
      source.close();
    });

    source.addEventListener("scan.failed", () => {
      update(() => ({ status: "failed" }));
      closed = true;
      source.close();
    });

    source.onerror = () => {
      // A completed stream closes from our side; anything else is a real failure.
      if (!closed) update((previous) => (previous.status === "complete" ? {} : { status: "failed" }));
    };

    return () => {
      closed = true;
      source.close();
    };
  }, [ready, scanId, queryClient]);

  return state.key === scanId ? state : fresh(scanId);
}
