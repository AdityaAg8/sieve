import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StreamStatus } from "@/lib/sse";

import { THROTTLE_MS, useStreamAnnouncement, type StreamCounts } from "./useStreamAnnouncement";

const NONE: StreamCounts = { confirmed: 0, needs_review: 0, dismissed: 0 };

type Props = {
  status: StreamStatus;
  decided: number;
  expected: number | null;
  counts: StreamCounts;
  failed: boolean;
};

function setup(initial: Partial<Props> = {}) {
  const props: Props = {
    status: "connecting",
    decided: 0,
    expected: 27,
    counts: NONE,
    failed: false,
    ...initial,
  };
  return renderHook(
    (p: Props) => useStreamAnnouncement(p.status, p.decided, p.expected, p.counts, p.failed),
    { initialProps: props },
  );
}

describe("useStreamAnnouncement", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("says nothing before the stream starts", () => {
    const { result } = setup();
    act(() => void vi.advanceTimersByTime(50));
    expect(result.current).toBe("");
  });

  it("throttles 30 events 30 ms apart to at most two progress announcements plus a final", () => {
    const { result, rerender } = setup({ status: "streaming" });

    const announced: string[] = [];
    const record = () => {
      const message = result.current;
      if (message && announced.at(-1) !== message) announced.push(message);
    };

    act(() => void vi.advanceTimersByTime(0));
    record();

    // 30 verdicts, 30 ms apart — the whole mocked stream, at its real spacing. The rerender and
    // the clock move in separate `act` blocks so effects are flushed before time advances.
    for (let index = 1; index <= 30; index += 1) {
      act(() => {
        rerender({
          status: "streaming",
          decided: index,
          expected: 27,
          counts: NONE,
          failed: false,
        });
      });
      act(() => void vi.advanceTimersByTime(30));
      record();
    }

    const progress = [...announced];
    expect(progress.length).toBeLessThanOrEqual(2);
    expect(progress.every((message) => message.startsWith("Triaging "))).toBe(true);

    act(() => {
      rerender({
        status: "complete",
        decided: 27,
        expected: 27,
        counts: { confirmed: 4, needs_review: 2, dismissed: 21 },
        failed: false,
      });
    });
    act(() => void vi.advanceTimersByTime(0));
    record();

    expect(result.current).toBe("Triage complete: 4 confirmed, 2 needs review, 21 dismissed.");
    expect(announced.length).toBeLessThanOrEqual(3);
  });

  it("keeps announcing progress once per second on a long stream", () => {
    const { result, rerender } = setup({ status: "streaming" });

    act(() => void vi.advanceTimersByTime(0));
    expect(result.current).toBe("Triaging 0 of 27.");

    act(() => {
      rerender({ status: "streaming", decided: 9, expected: 27, counts: NONE, failed: false });
    });
    act(() => void vi.advanceTimersByTime(THROTTLE_MS));
    expect(result.current).toBe("Triaging 9 of 27.");

    act(() => {
      rerender({ status: "streaming", decided: 18, expected: 27, counts: NONE, failed: false });
    });
    act(() => void vi.advanceTimersByTime(THROTTLE_MS));
    expect(result.current).toBe("Triaging 18 of 27.");
  });

  it("reads the final counts at the moment the stream completes", () => {
    const { result, rerender } = setup({ status: "streaming" });
    act(() => {
      rerender({
        status: "complete",
        decided: 3,
        expected: 3,
        counts: { confirmed: 1, needs_review: 0, dismissed: 2 },
        failed: false,
      });
    });
    act(() => void vi.advanceTimersByTime(0));
    expect(result.current).toBe("Triage complete: 1 confirmed, 0 needs review, 2 dismissed.");
  });

  it("says the scan could not be read when both paths fail", () => {
    const { result } = setup({ status: "failed", failed: true });
    act(() => void vi.advanceTimersByTime(0));
    expect(result.current).toBe("The scan could not be read.");
  });

  it("does not restart the throttle when a verdict arrives", () => {
    const setInterval = vi.spyOn(window, "setInterval");
    const { rerender } = setup({ status: "streaming" });

    for (let index = 1; index <= 10; index += 1) {
      act(() => {
        rerender({
          status: "streaming",
          decided: index,
          expected: 27,
          counts: NONE,
          failed: false,
        });
      });
      act(() => void vi.advanceTimersByTime(30));
    }

    expect(setInterval).toHaveBeenCalledTimes(1);
    setInterval.mockRestore();
  });
});
