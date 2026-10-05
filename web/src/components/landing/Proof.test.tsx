import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BenchSummary } from "@/lib/api";
import fixtureV1 from "@/mocks/fixtures/bench-summary.v1.json";
import { provisionalBench } from "@/mocks/handlers";

import { Proof } from "./Proof";

let data: BenchSummary | undefined;
vi.mock("@/lib/queries", () => ({ useBench: () => ({ data }) }));

const measured = fixtureV1 as unknown as BenchSummary;

/** The `<dt>`/`<dd>` pair for one slot, found by the slot's label. */
function slot(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  const cell = term.parentElement;
  if (!cell) throw new Error(`no slot for ${label}`);
  return cell;
}

describe("<Proof />", () => {
  beforeEach(() => {
    data = measured;
  });

  it("shows the four slots in order, each naming the tools it counts", () => {
    render(<Proof />);
    const labels = screen.getAllByRole("term").map((el) => el.firstChild?.textContent);
    expect(labels).toEqual([
      "Real tools sent to review",
      "YARA false positives dismissed",
      "Synthetic attacks caught",
      "Real attacks not dismissed",
    ]);
  });

  it("never claims a pipeline false-positive rate, nor a bare 'false positives removed'", () => {
    render(<Proof />);
    expect(screen.queryByText(/pipeline false-positive/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/false positives removed/i)).not.toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("leads with the share of real tools sent to review, beside YARA's rate on the same tools", () => {
    render(<Proof />);
    const first = within(slot("Real tools sent to review"));
    expect(first.getByText("0.35 [0.22, 0.49]")).toBeInTheDocument();
    expect(
      first.getByText(/real subset, holdout; Stage 1 at T_low\. YARA flags 24\.8% of the same tools/),
    ).toBeInTheDocument();
  });

  it("states the population the dismissal share was counted over", () => {
    render(<Proof />);
    const second = within(slot("YARA false positives dismissed"));
    expect(second.getByText("0.38 [0.18, 0.65]")).toBeInTheDocument(); // 10/26 = 0.3846
    expect(second.getByText(/real tools YARA wrongly flagged, holdout, n = 26/)).toBeInTheDocument();
  });

  it("shows the synthetic recall as k/n with the exact lower bound, never as a bare 100%", () => {
    render(<Proof />);
    const third = within(slot("Synthetic attacks caught"));
    expect(third.getByText("41/41")).toBeInTheDocument();
    expect(third.getByText(/synthetic holdout; exact 95% lower bound 0\.914/)).toBeInTheDocument();
  });

  it("puts MCPTox recall beside the share of its clean tools Sieve flags", () => {
    render(<Proof />);
    const fourth = within(slot("Real attacks not dismissed"));
    expect(fourth.getByText("1.00")).toBeInTheDocument();
    expect(
      fourth.getByText(/MCPTox, evaluation-only; Sieve flags 40% of MCPTox's clean tools/),
    ).toBeInTheDocument();
  });

  it("leaves a slot a dash with 'metric to confirm' when its key is absent", () => {
    const older = structuredClone(measured);
    delete (older.tables as Record<string, Record<string, unknown>>).holdout!.recall_cp;
    data = older;
    render(<Proof />);
    const third = within(slot("Synthetic attacks caught"));
    expect(third.getByText("—")).toBeInTheDocument();
    expect(third.getByText("metric to confirm")).toBeInTheDocument();
  });

  it("reads a dash in every slot while the benchmark is provisional", () => {
    data = provisionalBench;
    render(<Proof />);
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.getAllByText("metric to confirm")).toHaveLength(4);
  });
});
