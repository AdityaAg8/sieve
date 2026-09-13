import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setPrefersReducedMotion } from "@/test/setup";

import { Sieve, type SieveDatum } from "./Sieve";
import { MAX_TOTAL_MS, STAGGER_MS, sieveTimeline } from "./useSieveTimeline";

const datum = (id: string, verdict: SieveDatum["verdict"], flagged = false): SieveDatum => ({
  id,
  label: id,
  verdict,
  flaggedByScanner: flagged,
});

const SAMPLE: SieveDatum[] = [
  datum("weather/set_location", "confirmed", true),
  datum("db-query/export_csv", "confirmed", true),
  datum("notes/sync_notes", "needs_review", true),
  datum("weather/get_current", "dismissed"),
  datum("weather/get_forecast", "dismissed"),
];

describe("sieveTimeline", () => {
  it("staggers tiles by 28 ms", () => {
    const timeline = sieveTimeline(27);
    expect(timeline.staggerMs).toBe(STAGGER_MS);
    expect(timeline.delayFor(0)).toBe(0);
    expect(timeline.delayFor(1)).toBe(28);
    expect(timeline.delayFor(26)).toBe(26 * 28);
  });

  it("keeps 27 tiles inside the 2.4 s budget, computed rather than asserted", () => {
    const timeline = sieveTimeline(27);
    expect(timeline.totalMs).toBeLessThanOrEqual(MAX_TOTAL_MS);
    expect(timeline.totalMs).toBe(26 * 28 + timeline.fixedMs);
  });

  it("compresses the stagger rather than overrunning the budget for a large scan", () => {
    const timeline = sieveTimeline(500);
    expect(timeline.totalMs).toBeLessThanOrEqual(MAX_TOTAL_MS);
    expect(timeline.staggerMs).toBeLessThan(STAGGER_MS);
  });

  it("puts line contact one fall after a tile's own delay", () => {
    const timeline = sieveTimeline(4);
    expect(timeline.lineContactFor(2)).toBe(timeline.delayFor(2) + timeline.fallMs);
  });

  it("has no stagger for a single tile", () => {
    expect(sieveTimeline(1).staggerMs).toBe(0);
    expect(sieveTimeline(0).totalMs).toBe(sieveTimeline(0).fixedMs);
  });
});

describe("<Sieve />", () => {
  it("renders every tile in its final tray immediately under reduced motion", async () => {
    setPrefersReducedMotion(true);
    const onComplete = vi.fn();
    render(<Sieve data={SAMPLE} autoplay replayable onComplete={onComplete} />);

    const figure = screen.getByRole("figure", { name: /sieve/i });
    expect(figure).toHaveAttribute("data-motion", "static");

    for (const item of SAMPLE) {
      const tile = screen.getByTestId(`sieve-tile-${item.id}`);
      expect(tile).toHaveAttribute("data-tray", item.verdict);
      expect(tile).toHaveAttribute("data-settled", "true");
    }

    await waitFor(() => expect(onComplete).toHaveBeenCalled());
  });

  it("reads the flagged and confirmed counts out of the data, never a literal", () => {
    setPrefersReducedMotion(true);
    render(<Sieve data={SAMPLE} autoplay replayable />);

    // Three of the five sample tiles carry a scanner finding; two are confirmed.
    expect(screen.getByTestId("sieve-count-flagged")).toHaveTextContent("3");
    expect(screen.getByTestId("sieve-count-confirmed")).toHaveTextContent("2");
    expect(screen.getByTestId("sieve-tray-count-needs_review")).toHaveTextContent("1");
    expect(screen.getByTestId("sieve-tray-count-dismissed")).toHaveTextContent("2");
  });

  it("falls back to the tile count when no datum says the scanner flagged it", () => {
    setPrefersReducedMotion(true);
    render(<Sieve data={[datum("a", "confirmed"), datum("b", "dismissed")]} autoplay replayable />);
    expect(screen.getByTestId("sieve-count-flagged")).toHaveTextContent("2");
  });

  it("re-runs the pass when replay is pressed", async () => {
    setPrefersReducedMotion(true);
    const onComplete = vi.fn();
    render(<Sieve data={SAMPLE} autoplay replayable onComplete={onComplete} />);
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("button", { name: /replay/i }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(2));
  });

  it("labels each tile with the real tool id in monospace", () => {
    setPrefersReducedMotion(true);
    render(<Sieve data={SAMPLE} autoplay replayable />);
    expect(screen.getByText("weather/set_location")).toBeInTheDocument();
  });
});
