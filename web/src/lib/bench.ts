/**
 * The shape the web reads out of `GET /api/v1/bench/summary`.
 *
 * `BenchSummary.headline` and `BenchSummary.tables` are free-form objects in the frozen OpenAPI
 * contract, so this file is the web's half of that agreement: the keys below are what the
 * benchmark page and the landing proof block look for, and every one of them is optional. A key
 * that is missing renders as `—` with "metric to confirm" beside it — never as a zero, never as
 * a plausible placeholder (PRD §14.2).
 *
 * The API owns the producer; these are the keys this view requires of it.
 */
import type { BenchSummary } from "./api";

/** A point estimate with a 95 % cluster-bootstrap interval. */
export type Interval = { value: number; lo: number; hi: number };

export type Headline = {
  /** Pipeline false-positive rate on the real subset — the headline number (PRD §4.3). */
  fpr_pipeline_real?: Interval;
  /** The synthetic-inclusive figure, reported second and labelled. */
  fpr_pipeline_all?: Interval;
  /** Reduction in scanner false positives, with the paired-bootstrap interval. */
  fp_reduction_vs_baseline?: Interval;
  /** McNemar's exact test p-value beside the reduction figure. */
  mcnemar_p?: number;
  holdout_recall?: Interval;
  real_poisoned_recall?: Interval;
  /** Which baseline the comparison is against, e.g. "cisco_yara@4.8.0". */
  baseline_name?: string;
};

export type AblationRow = {
  id: number | string;
  name: string;
  recall?: Interval;
  fpr?: Interval;
  macro_f1?: Interval;
  note?: string;
};

export type BreakdownRow = { bucket: string; recall?: number; fpr?: number; n?: number };

export type ConfusionMatrix = {
  labels: string[];
  counts: number[][];
  /** Cell the page calls out explicitly, as [rowIndex, colIndex]. */
  called_out?: [number, number];
};

export type Agreement = {
  overall?: number;
  real_subset?: number;
  per_class?: { class: string; observed?: number; ac1?: number }[];
  /** Not an inter-rater κ. Labelled as its own statistic, never as "κ". */
  blind_vs_generator?: number;
};

export type MinimalPairRow = { tier: string; m11?: number; n?: number };

export type HumanReference = { label: string; recall: number; fpr: number };

export type HoldoutLogRow = { git_sha: string; utc: string; rows: number; reason: string };

export type Tables = {
  ablations?: AblationRow[];
  per_tier?: BreakdownRow[];
  per_concealment?: BreakdownRow[];
  confusion?: ConfusionMatrix;
  agreement?: Agreement;
  minimal_pairs?: MinimalPairRow[];
  human_reference?: HumanReference[];
  test_opened?: HoldoutLogRow[];
};

export function headlineOf(summary: BenchSummary | undefined): Headline {
  return (summary?.headline ?? {}) as Headline;
}

export function tablesOf(summary: BenchSummary | undefined): Tables {
  return (summary?.tables ?? {}) as Tables;
}

/** True while `make bench` has not produced numbers for this deployment. */
export function isProvisional(summary: BenchSummary | undefined): boolean {
  return summary?.provisional !== false;
}

/** The one string the app is allowed to show where a number has not been measured. */
export const NO_NUMBER = "—";
export const NO_NUMBER_LABEL = "metric to confirm";

export function formatRatio(value: number | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : NO_NUMBER;
}

export function formatPercent(value: number | undefined, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(digits)}%`
    : NO_NUMBER;
}

/** "0.31 [0.22, 0.42]" — the point estimate first, then the interval, or `—`. */
export function formatInterval(interval: Interval | undefined, digits = 2): string {
  if (!interval || typeof interval.value !== "number") return NO_NUMBER;
  const point = interval.value.toFixed(digits);
  if (typeof interval.lo !== "number" || typeof interval.hi !== "number") return point;
  return `${point} [${interval.lo.toFixed(digits)}, ${interval.hi.toFixed(digits)}]`;
}
