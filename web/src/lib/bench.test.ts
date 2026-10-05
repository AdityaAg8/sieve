import { describe, expect, it } from "vitest";

import fixture from "@/mocks/fixtures/bench-summary.json";
import fixtureV1 from "@/mocks/fixtures/bench-summary.v1.json";
import { provisionalBench } from "@/mocks/handlers";

import type { BenchSummary } from "./api";
import {
  NO_NUMBER,
  ablationsOf,
  formatCount,
  formatInterval,
  formatMeanStd,
  headlineOf,
  holdoutLogOf,
  isProvisional,
  labelAuditOf,
  minimalPairsOf,
  perConcealmentOf,
  perTierOf,
  tablesOf,
} from "./bench";

/**
 * `bench-summary.json` is what `GET /bench/summary` serves over results files written by their
 * real producers (`python -m tests.api.bench_fixture`): the ablation matrix (H5), the sealed
 * report (H3), the label audit (F3) and the opening log. These tests read the producers' own
 * keys out of it, so a renamed key fails here rather than rendering a quiet dash.
 */
const measured = fixture as unknown as BenchSummary;
const tables = tablesOf(measured);
const report = tables.holdout!;
const ablations = tables.ablations!;

describe("headlineOf", () => {
  it("reads the sealed report's own keys", () => {
    const headline = headlineOf(measured);
    expect(headline.fpr_pipeline_real).toEqual(report.fpr_pipeline_real);
    expect(headline.fpr_stage1_real).toEqual(report.fpr_stage1_real);
    expect(headline.holdout_recall).toEqual(report.recall_interval);
    expect(headline.real_poisoned_recall).toEqual(
      report.headline_report?.real_poisoned?.recall_stage1,
    );
    expect(headline.release_gate_passed).toBe(report.release_gate_passed);
  });

  it("maps the triage FP reduction's relative figure to an interval with its McNemar p", () => {
    const withReduction = structuredClone(measured);
    const own = tablesOf(withReduction).holdout!.headline_report!.holdout!;
    own.fp_reduction_triage = {
      relative: 0.62,
      relative_lo: 0.44,
      relative_hi: 0.78,
      mcnemar_p: 0.0031,
    };
    const headline = headlineOf(withReduction);
    expect(headline.fp_reduction).toEqual({ point: 0.62, lo: 0.44, hi: 0.78 });
    expect(headline.mcnemar_p).toBe(0.0031);
  });

  it("is empty — every slot a dash — before the report exists", () => {
    expect(headlineOf(provisionalBench)).toEqual({});
    expect(formatInterval(headlineOf(provisionalBench).holdout_recall)).toBe(NO_NUMBER);
  });
});

describe("the ablation matrix", () => {
  it("keeps every row, deferred ones included, in matrix order", () => {
    const rows = ablationsOf(measured);
    expect(rows.map((r) => r.row)).toEqual(ablations.map((r) => r.row));
    const deferred = rows.filter((r) => r.status === "deferred");
    expect(deferred.map((r) => r.row)).toEqual([6, 7, 8]);
    for (const row of deferred) {
      expect(row.reason).toMatch(/D8/);
      expect(row.oof_recall_at_t_low).toBeUndefined();
    }
  });

  it("reads minimal-pair accuracy off the scored rows, not the breakdown views", () => {
    const pairs = minimalPairsOf(measured);
    const expected = ablations.filter(
      (r) => typeof r.minimal_pair_accuracy === "number" && (r.breakdown_of ?? []).length === 0,
    );
    expect(pairs.rows.map((r) => r.row)).toEqual(expected.map((r) => r.row));
    expect(pairs.rows[0]?.m11).toBe(expected[0]?.minimal_pair_accuracy);
    // Row 14's judge half (row 6) is deferred, and the table says so rather than dropping it.
    expect(pairs.deferred).toEqual([6]);
  });
});

describe("breakdowns", () => {
  it("turns the report's per-tier mapping into one row per bucket", () => {
    const rows = perTierOf(measured);
    expect(rows.map((r) => r.bucket)).toEqual(Object.keys(report.per_tier ?? {}));
    const first = rows[0]!;
    const source = report.per_tier![first.bucket]!;
    expect(first).toEqual({
      bucket: first.bucket,
      recall: source.recall_at_t_low,
      fpr: source.fpr_pipeline,
      n: source.n,
    });
    expect(perConcealmentOf(measured).length).toBe(
      Object.keys(report.per_concealment ?? {}).length,
    );
  });
});

describe("the label audit and the opening log", () => {
  it("reads the 104-row test-ID audit", () => {
    const audit = labelAuditOf(measured);
    expect(audit.test_id?.n).toBe(104);
    expect(audit.test_id?.kappa).toBe(tables.label_audit?.test_id?.kappa);
  });

  it("reads rows_evaluated off every log line", () => {
    const log = holdoutLogOf(measured);
    expect(log).toHaveLength(1);
    expect(log[0]?.rows_evaluated).toBe(tables.test_opened?.[0]?.rows_evaluated);
  });
});

describe("formatting", () => {
  it("prints a producer interval as point [lo, hi]", () => {
    expect(formatInterval({ point: 0.31, lo: 0.22, hi: 0.42 })).toBe("0.31 [0.22, 0.42]");
    expect(formatInterval({ point: null, lo: null, hi: null })).toBe(NO_NUMBER);
    expect(formatInterval({ point: 0.31, lo: null, hi: null })).toBe("0.31");
  });

  it("prints a mean across seeds with its sample std, or the one value, or a dash", () => {
    expect(formatMeanStd(0.861, 0.012)).toBe("0.86 ± 0.01");
    expect(formatMeanStd(0.861, null)).toBe("0.86");
    expect(formatMeanStd(null, null)).toBe(NO_NUMBER);
  });
});

describe("isProvisional", () => {
  it("is true only while the API says no results files exist", () => {
    expect(isProvisional(provisionalBench)).toBe(true);
    expect(isProvisional(measured)).toBe(false);
  });
});

/**
 * The v1 audit keys (`baseline_fpr_real`, `fp_dismissed_real_triage`, `recall_cp`) come from the
 * measured fixture: `bench-summary.v1.json` mirrors `bench/results/holdout.json`.
 */
const measuredV1 = fixtureV1 as unknown as BenchSummary;
const reportV1 = tablesOf(measuredV1).holdout!;

describe("headlineOf, the audited population labels", () => {
  it("reads the review share, YARA's rate on the same tools and the dismissal share", () => {
    const headline = headlineOf(measuredV1);
    expect(headline.real_review_share).toEqual(reportV1.fpr_stage1_real);
    expect(headline.baseline_fpr_real).toEqual(reportV1.baseline_fpr_real);
    expect(headline.baseline_fpr_real).toMatchObject({ k: 26, n: 105 });
    expect(headline.fp_dismissed_real).toEqual(reportV1.fp_dismissed_real_triage);
    expect(headline.fp_dismissed_real).toMatchObject({ point: 0.385, lo: 0.182, hi: 0.65, n: 26 });
  });

  it("reads the exact lower bound on the synthetic holdout's recall", () => {
    expect(headlineOf(measuredV1).holdout_recall_cp).toEqual({ lo: 0.914, hi: 1, k: 41, n: 41 });
  });

  it("reads MCPTox's clean-tool flag rate off the headline model's out-of-distribution report", () => {
    const clean = reportV1.headline_report?.test_ood?.mcptox_clean;
    expect(clean?.point).toBeCloseTo(0.402, 3);
    expect(headlineOf(measuredV1).mcptox_clean_fpr).toEqual(clean);
  });

  it("leaves a slot absent when an older report does not carry its key", () => {
    // The mini-50 fixture predates the three keys the audit added.
    const older = headlineOf(measured);
    expect(older).not.toHaveProperty("baseline_fpr_real");
    expect(older).not.toHaveProperty("fp_dismissed_real");
    expect(older).not.toHaveProperty("holdout_recall_cp");

    const stripped = structuredClone(measuredV1);
    delete tablesOf(stripped).holdout!.headline_report!.test_ood;
    expect(headlineOf(stripped)).not.toHaveProperty("mcptox_clean_fpr");
  });
});

describe("formatCount", () => {
  it("prints k/n only when both are present", () => {
    expect(formatCount({ k: 41, n: 41 })).toBe("41/41");
    expect(formatCount({ n: 41 })).toBe(NO_NUMBER);
    expect(formatCount(undefined)).toBe(NO_NUMBER);
  });
});

describe("breakdown rows with no poisoned tools", () => {
  it("carries the poisoned count so a chart can tell 'no attacks' from 'missed them all'", () => {
    const rows = perTierOf({
      tables: {
        holdout: {
          per_tier: {
            hard: { n: 13, n_poisoned: 13, recall_at_t_low: 1 },
            unscored: { n: 128, n_poisoned: 0, recall_at_t_low: null, fpr_pipeline: 0 },
          },
        },
      },
    } as unknown as BenchSummary);
    expect(rows.find((r) => r.bucket === "hard")?.n_poisoned).toBe(13);
    const unscored = rows.find((r) => r.bucket === "unscored")!;
    expect(unscored.n_poisoned).toBe(0);
    expect(unscored.recall).toBeUndefined();
  });
});
