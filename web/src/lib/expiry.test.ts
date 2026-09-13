import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useHasPassed } from "./expiry";

afterEach(() => vi.useRealTimers());

describe("useHasPassed", () => {
  it("is false for a moment still ahead", () => {
    const { result } = renderHook(() => useHasPassed(new Date(Date.now() + 60_000).toISOString()));
    expect(result.current).toBe(false);
  });

  it("is true for a moment already behind", () => {
    const { result } = renderHook(() => useHasPassed(new Date(Date.now() - 1).toISOString()));
    expect(result.current).toBe(true);
  });

  it("turns true on its own when the moment arrives", async () => {
    vi.useFakeTimers();
    // Computed once: a deadline recomputed from `Date.now()` on every render is a deadline that
    // never arrives, and the test would pass for the wrong reason in reverse.
    const deadline = new Date(Date.now() + 5_000).toISOString();
    const { result } = renderHook(() => useHasPassed(deadline));
    expect(result.current).toBe(false);

    // React's own scheduler runs on the same faked timers, so the re-render needs the clock to
    // keep moving after the deadline's timer has fired.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_001);
    });
    expect(result.current).toBe(true);
  });

  it("is false for a missing or unparseable timestamp, never true by accident", () => {
    expect(renderHook(() => useHasPassed(null)).result.current).toBe(false);
    expect(renderHook(() => useHasPassed(undefined)).result.current).toBe(false);
    expect(renderHook(() => useHasPassed("not a date")).result.current).toBe(false);
  });

  it("does not treat a deadline beyond the timer limit as already passed", () => {
    const farFuture = new Date(Date.now() + 2 ** 31 + 60_000).toISOString();
    const { result } = renderHook(() => useHasPassed(farFuture));
    expect(result.current).toBe(false);
  });
});
