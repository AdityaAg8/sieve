import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Headline } from "@/lib/bench";

import { HeadlineTable, NO_TEST_DEFINED } from "./HeadlineTable";

const interval = { value: 0.31, lo: 0.22, hi: 0.42 };

function rowNamed(slice: string): HTMLElement {
  const cell = screen.getByText(slice);
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${slice}`);
  return row;
}

describe("<HeadlineTable />", () => {
  /**
   * The real `GET /bench/summary` answers from `bench/results/`, and before `make bench` has run
   * a paired comparison it carries no `mcnemar_p` at all. "—" there has to mean "nobody has
   * measured this yet"; it must not also mean "this row has no significance test", because the
   * two ask for completely different things from the reader.
   */
  it("says a row has no significance test rather than printing an em dash", () => {
    render(<HeadlineTable headline={{ fpr_pipeline_real: interval } satisfies Headline} />);
    expect(within(rowNamed("real subset (headline)")).getByText(NO_TEST_DEFINED)).toBeInTheDocument();
  });

  it("marks the reduction's test as still to confirm when the summary carries no p-value", () => {
    render(
      <HeadlineTable
        headline={{ fp_reduction_vs_baseline: interval, baseline_name: "cisco_yara@4.8.0" }}
      />,
    );
    const row = within(rowNamed("vs cisco_yara@4.8.0"));
    expect(row.getByText("—")).toBeInTheDocument();
    expect(row.getByText("metric to confirm")).toBeInTheDocument();
    expect(row.queryByText(NO_TEST_DEFINED)).not.toBeInTheDocument();
  });

  it("prints the p-value once the benchmark has one", () => {
    render(
      <HeadlineTable headline={{ fp_reduction_vs_baseline: interval, mcnemar_p: 0.0013 }} />,
    );
    expect(screen.getByText("McNemar p = 0.0013")).toBeInTheDocument();
  });
});
