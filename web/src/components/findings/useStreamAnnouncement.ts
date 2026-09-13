"use client";

import { useEffect, useRef, useState } from "react";

import type { Verdict } from "@/lib/api";
import type { StreamStatus } from "@/lib/sse";

/** At most one announcement per second while the stream runs. */
const THROTTLE_MS = 1000;

/**
 * What a screen reader is told while the results page fills.
 *
 * Two properties matter. The region has to exist from **first paint** — a live region mounted at
 * the moment it first has content is not announced at all, because the assistive technology has
 * nothing to observe a change against. And it must not speak 29 times: a verdict every 30 ms
 * would be unusable, so progress is throttled to roughly once a second and the settled result is
 * announced once, in full.
 */
export function useStreamAnnouncement(
  status: StreamStatus,
  decided: number,
  expected: number | null,
  counts: Record<Verdict, number>,
  failed: boolean,
): string {
  const [message, setMessage] = useState("");

  // The throttled interval reads progress through this ref, so a verdict arriving every 30 ms
  // never restarts the throttle. Assigned in its own effect, declared before the one that reads
  // it, so the value is current by the time the interval first fires.
  const latest = useRef({ decided, expected });
  useEffect(() => {
    latest.current = { decided, expected };
  }, [decided, expected]);

  useEffect(() => {
    if (failed) {
      const timer = window.setTimeout(
        () => setMessage("The scan could not be read."),
        0,
      );
      return () => window.clearTimeout(timer);
    }

    if (status === "complete") {
      const timer = window.setTimeout(
        () =>
          setMessage(
            `Triage complete: ${counts.confirmed} confirmed, ${counts.needs_review} needs review, ` +
              `${counts.dismissed} dismissed.`,
          ),
        0,
      );
      return () => window.clearTimeout(timer);
    }

    if (status !== "streaming") return;

    const announce = () => {
      const now = latest.current;
      setMessage(`Triaging ${now.decided} of ${now.expected ?? "—"}.`);
    };
    announce();
    const interval = window.setInterval(announce, THROTTLE_MS);
    return () => window.clearInterval(interval);
  }, [status, failed, counts.confirmed, counts.needs_review, counts.dismissed]);

  return message;
}
