import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ablationsOf, type AblationRow } from "@/lib/bench";
import fixture from "@/mocks/fixtures/bench-summary.json";
import type { BenchSummary } from "@/lib/api";

import { AblationTable } from "./AblationTable";

const rows = ablationsOf(fixture as unknown as BenchSummary);

function rowFor(id: number): HTMLElement {
  const row = screen
    .getAllByTestId("ablation-row")
    .find((tr) => tr.getAttribute("data-row") === String(id));
  if (!row) throw new Error(`no ablation row ${id}`);
  return row;
}

describe("<AblationTable />", () => {
  it("renders every row of the matrix", () => {
    render(<AblationTable rows={rows} />);
    expect(screen.getAllByTestId("ablation-row")).toHaveLength(rows.length);
  });

  /**
   * D8: the judge is deferred to v1.1. A deferred row has no metrics at all in
   * `ablations.json`, and it must read as deferred — grey, saying why — never as zeros and
   * never as a dash that would claim the number is merely unmeasured.
   */
  it("shows a deferred judge row as deferred, with its reason and no number", () => {
    render(<AblationTable rows={rows} />);
    const deferred = rowFor(6);
    expect(deferred).toHaveAttribute("data-status", "deferred");
    expect(within(deferred).getByText("Deferred")).toBeInTheDocument();
    expect(within(deferred).getByText(/D8: the LLM judge is deferred to v1\.1/)).toBeInTheDocument();
    expect(deferred.textContent).not.toMatch(/0\.\d\d/);
    expect(within(deferred).queryByText("—")).not.toBeInTheDocument();
  });

  it("prints macro-F1 as the mean across seeds with its standard deviation", () => {
    const row: AblationRow = {
      row: 4,
      name: "Primary encoder",
      status: "active",
      seeds: [0, 1, 2],
      single_seed: false,
      macro_f1_mean: 0.861,
      macro_f1_std: 0.012,
      oof_recall_at_t_low: { point: 0.94, lo: 0.9, hi: 0.97, std: 0.01 },
      fpr_pipeline: { point: 0.01, lo: 0.0, hi: 0.02, std: 0.0 },
      fpr_stage1_real_at_t_low: { point: 0.26, lo: 0.2, hi: 0.33, std: 0.15 },
    };
    render(<AblationTable rows={[row]} />);
    const tr = rowFor(4);
    expect(within(tr).getByText("0.86 ± 0.01")).toBeInTheDocument();
    expect(within(tr).getByText("0.94 [0.90, 0.97]")).toBeInTheDocument();
    // The column is the real review load, never the auto-confirm rate (v1 audit F1).
    expect(within(tr).getByText("0.26 [0.20, 0.33]")).toBeInTheDocument();
    expect(within(tr).queryByText("0.01 [0.00, 0.02]")).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Real tools sent to review/ })).toBeVisible();
    expect(within(tr).getByText("3 seeds")).toBeInTheDocument();
  });

  it("marks a single-seed row as such", () => {
    render(<AblationTable rows={rows} />);
    expect(within(rowFor(15)).getByText("single seed")).toBeInTheDocument();
  });

  it("says when a breakdown row's judge half is deferred", () => {
    render(<AblationTable rows={rows} />);
    expect(within(rowFor(10)).getByText(/\(row 6\) is deferred/)).toBeInTheDocument();
  });
});
