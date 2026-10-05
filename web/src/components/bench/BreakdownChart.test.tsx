import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BreakdownRow } from "@/lib/bench";

import { BreakdownChart, NOT_APPLICABLE, breakdownSeries } from "./BreakdownChart";

/**
 * The `unscored` tier holds clean tools only: it has no poisoned rows, so recall there is
 * undefined. The producer writes 0.0 for it, and a chart that draws that 0.0 says "Sieve caught
 * none of them" about a bucket that contains no attacks.
 */
const rows: BreakdownRow[] = [
  { bucket: "hard", recall: 1, fpr: 0, n: 13, n_poisoned: 13 },
  { bucket: "unscored", recall: 0, fpr: 0.45, n: 128, n_poisoned: 0 },
];

function recallCell(bucket: string): HTMLElement {
  const label = screen
    .getAllByTestId("tier-bucket")
    .find((el) => el.textContent === bucket);
  const row = label?.closest("tr");
  if (!row) throw new Error(`no row for ${bucket}`);
  return within(row).getAllByRole("cell")[1]!;
}

function renderChart(data: BreakdownRow[]) {
  render(
    <BreakdownChart id="tier" title="By tier" lede="Per tier." bucketLabel="Tier" rows={data} />,
  );
}

describe("<BreakdownChart />", () => {
  it("renders n/a, not 0.0%, for a tier with no poisoned rows", () => {
    renderChart(rows);
    expect(recallCell("unscored")).toHaveTextContent(NOT_APPLICABLE);
    expect(recallCell("unscored")).not.toHaveTextContent("0.0%");
    expect(recallCell("hard")).toHaveTextContent("100.0%");
  });

  it("renders n/a when recall is missing even if the poisoned count is not carried", () => {
    renderChart([{ bucket: "unscored", fpr: 0.45, n: 128 }]);
    expect(recallCell("unscored")).toHaveTextContent(NOT_APPLICABLE);
  });

  it("still shows the false-positive rate of a bucket whose recall is n/a", () => {
    renderChart(rows);
    const row = recallCell("unscored").closest("tr")!;
    expect(row).toHaveTextContent("45.0%");
  });
});

describe("breakdownSeries", () => {
  it("omits the recall bar (null) for a bucket with no poisoned rows, never 0", () => {
    const [hard, unscored] = breakdownSeries(rows);
    expect(hard?.recall).toBe(1);
    expect(unscored?.recall).toBeNull();
    expect(unscored?.fpr).toBe(0.45);
  });

  it("omits the recall bar when recall is undefined", () => {
    expect(breakdownSeries([{ bucket: "x", n: 3 }])[0]?.recall).toBeNull();
  });

  it("keeps a real zero recall when the bucket does hold attacks", () => {
    expect(breakdownSeries([{ bucket: "x", recall: 0, n: 3, n_poisoned: 3 }])[0]?.recall).toBe(0);
  });
});
