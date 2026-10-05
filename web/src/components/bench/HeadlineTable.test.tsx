import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BenchSummary } from "@/lib/api";
import { headlineOf, type Headline } from "@/lib/bench";
import fixtureV1 from "@/mocks/fixtures/bench-summary.v1.json";

import { HeadlineTable, NO_TEST_DEFINED } from "./HeadlineTable";

const interval = { point: 0.31, lo: 0.22, hi: 0.42 };

const REVIEW = "real subset, holdout; Stage 1 at T_low";
const DISMISSED_REAL = "real tools YARA wrongly flagged, holdout (n = 26)";
const DISMISSED_ALL = "all holdout negatives, real and synthetic";
const AUTO_CONFIRM = "Stage 1 only, real subset, holdout (review band excluded)";
const SYNTHETIC = "synthetic holdout attacks, recall at T_low";
const MCPTOX = "real poisoned, MCPTox (evaluation-only), Stage 1 at T_low";

function rowNamed(slice: string): HTMLElement {
  const cell = screen.getByText(slice);
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${slice}`);
  return row;
}

const measured: Headline = headlineOf(fixtureV1 as unknown as BenchSummary);

describe("<HeadlineTable />", () => {
  /**
   * The real `GET /bench/summary` answers from `bench/results/`, and before `make bench` has run
   * a paired comparison it carries no `mcnemar_p` at all. "—" there has to mean "nobody has
   * measured this yet"; it must not also mean "this row has no significance test", because the
   * two ask for completely different things from the reader.
   */
  it("says a row has no significance test rather than printing an em dash", () => {
    render(<HeadlineTable headline={{ fp_dismissed_real: { ...interval, n: 26 } } satisfies Headline} />);
    expect(within(rowNamed(DISMISSED_REAL)).getByText(NO_TEST_DEFINED)).toBeInTheDocument();
  });

  it("marks the all-negatives comparison's test as still to confirm without a p-value", () => {
    render(<HeadlineTable headline={{ fp_reduction: interval }} />);
    const row = within(rowNamed(DISMISSED_ALL));
    expect(row.getByText("0.31 [0.22, 0.42]")).toBeInTheDocument();
    expect(row.getByText("—")).toBeInTheDocument();
    expect(row.getByText("metric to confirm")).toBeInTheDocument();
    expect(row.queryByText(NO_TEST_DEFINED)).not.toBeInTheDocument();
  });

  it("puts the McNemar p beside the all-negatives figure it was computed on, and nowhere else", () => {
    render(<HeadlineTable headline={{ ...measured, mcnemar_p: 0.0013 }} />);
    expect(screen.getAllByText("McNemar p = 0.0013")).toHaveLength(1);
    expect(within(rowNamed(DISMISSED_ALL)).getByText("McNemar p = 0.0013")).toBeInTheDocument();
    expect(within(rowNamed(DISMISSED_REAL)).queryByText(/McNemar/)).not.toBeInTheDocument();
  });

  it("states the population of every row and never leads with a pipeline FP rate", () => {
    render(<HeadlineTable headline={measured} />);
    expect(screen.queryByText(/false positives removed/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Pipeline false-positive rate")).not.toBeInTheDocument();
    for (const slice of [REVIEW, DISMISSED_REAL, DISMISSED_ALL, SYNTHETIC, MCPTOX]) {
      expect(screen.getByText(slice)).toBeInTheDocument();
    }
  });

  it("shows the real review share beside YARA's rate on the same tools", () => {
    render(<HeadlineTable headline={measured} />);
    const row = within(rowNamed(REVIEW));
    expect(row.getByText("Real tools sent to review")).toBeInTheDocument();
    expect(row.getByText("0.35 [0.22, 0.49]")).toBeInTheDocument();
    expect(row.getByText("YARA flags 24.8% (26/105) of the same tools")).toBeInTheDocument();
  });

  it("shows the dismissal of YARA's real false positives with its population size", () => {
    render(<HeadlineTable headline={measured} />);
    const row = within(rowNamed(DISMISSED_REAL));
    expect(row.getByText("YARA false positives dismissed")).toBeInTheDocument();
    expect(row.getByText("0.38 [0.18, 0.65]")).toBeInTheDocument(); // 10/26 = 0.3846
  });

  it("shows M1 as not measured, with the Stage-1 auto-confirm count labelled as such", () => {
    render(<HeadlineTable headline={measured} />);
    expect(screen.getByText("not measured: judge deferred (v1.1)")).toBeInTheDocument();
    const row = within(rowNamed(AUTO_CONFIRM));
    expect(row.getByText("Real tools auto-confirmed")).toBeInTheDocument();
    expect(row.getByText("0/105")).toBeInTheDocument();
  });

  it("shows the synthetic recall as k/n with its exact lower bound, and the release gate", () => {
    render(<HeadlineTable headline={{ ...measured, release_gate_passed: false }} />);
    const row = within(rowNamed(SYNTHETIC));
    expect(row.getByText("Synthetic attacks caught")).toBeInTheDocument();
    expect(row.getByText("41/41")).toBeInTheDocument();
    expect(row.getByText("Exact 95% lower bound 0.914")).toBeInTheDocument();
    expect(row.getByText("Release gate not met")).toBeInTheDocument();
  });

  it("shows MCPTox recall beside the share of its clean tools Sieve flags, with no [1.00, 1.00]", () => {
    render(<HeadlineTable headline={measured} />);
    const row = within(rowNamed(MCPTOX));
    expect(row.getByText("Real attacks not dismissed")).toBeInTheDocument();
    expect(row.getByText("1.00")).toBeInTheDocument();
    expect(row.getByText("Sieve flags 40% of MCPTox's clean tools")).toBeInTheDocument();
  });

  it("leaves a figure it was not given as a dash, never as a zero", () => {
    render(<HeadlineTable headline={{}} />);
    const row = within(rowNamed(SYNTHETIC));
    expect(row.getAllByText("—").length).toBeGreaterThan(0);
    expect(row.queryByText("0/0")).not.toBeInTheDocument();
  });
});
