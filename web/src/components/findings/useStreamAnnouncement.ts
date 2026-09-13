"use client";

import { useEffect, useRef, useState } from "react";

import type { Verdict } from "@/lib/api";
import type { StreamStatus } from "@/lib/sse";

/** At most one progress announcement per second while the stream runs. */
export const THROTTLE_MS = 1000;

export type StreamCounts = Record<Verdict, number>;

/**
 * What a screen reader is told while the results page fills.
 *
 * Three properties matter, and the first two are easy to get wrong.
 *
 * 1. The region has to exist from **first paint** — a live region mounted at the moment it first
 *    has content is never announced, because the assistive technology has nothing to observe the
 *    change against. `ResultsScreen` mounts it unconditionally; this hook only supplies its text.
 * 2. The throttle has to survive the events. 29 verdicts arrive in under a second, so the
 *    interval is started **once per stream** — this effect depends on `status` and `failed` and
 *    on nothing that changes per event — and each tick reads the newest progress through a ref.
 *    An effect that depended on the counts would tear down and re-announce on every verdict,
 *    which is the same as having no throttle at all.
 * 3. The settled result is announced once, in full.
 */
export function useStreamAnnouncement(
  status: StreamStatus,
  decided: number,
  expected: number | null,
  counts: StreamCounts,
  failed: boolean,
): string {
  const [message, setMessage] = useState("");

  // Assigned in its own effect, declared before the one that reads it, so the value is current
  // by the time the first tick fires.
  const latest = useRef({ decided, expected, counts });
  useEffect(() => {
    latest.current = { decided, expected, counts };
  }, [decided, expected, counts]);

  useEffect(() => {
    if (failed) {
      const timer = window.setTimeout(() => setMessage("The scan could not be read."), 0);
      return () => window.clearTimeout(timer);
    }

    if (status === "complete") {
      const timer = window.setTimeout(() => {
        const { confirmed, needs_review, dismissed } = latest.current.counts;
        setMessage(
          `Triage complete: ${confirmed} confirmed, ${needs_review} needs review, ` +
            `${dismissed} dismissed.`,
        );
      }, 0);
      return () => window.clearTimeout(timer);
    }

    if (status !== "streaming") return;

    const announce = () => {
      const now = latest.current;
      setMessage(`Triaging ${now.decided} of ${now.expected ?? "—"}.`);
    };

    // Deferred rather than called inline: the first announcement is still a callback, so the
    // effect body itself never sets state.
    const first = window.setTimeout(announce, 0);
    const interval = window.setInterval(announce, THROTTLE_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(interval);
    };
  }, [status, failed]);

  return message;
}
