/**
 * The shape the web reads out of `GET /api/v1/bench/summary`.
 *
 * `BenchSummary.headline` and `BenchSummary.tables` are free-form objects in the frozen OpenAPI
 * contract. The API serves each `bench/results/` file under `tables`, keyed by what it is and
 * carrying **its producer's own keys** (training plan I5, finding F73):
 *
 * - `tables.ablations` — the rows of `ablations.json`, written by `bench.ablations.run` (H5);
 * - `tables.holdout` — `holdout.json` from `bench.report_final` (H3), minus the per-model bulk,
 *   plus the pre-registered headline model's own report as `headline_report`;
 * - `tables.label_audit` — `label_audit.json` from `bench.label.audit` (F3);
 * - `tables.ensemble_agreement` — `ensemble_agreement.json` from `bench.label.vote` (E5);
 * - `tables.test_opened` — the lines of `test_opened.log`.
 *
 * The types below mirror those producers and every key is optional. A key that is missing
 * renders as `—` with "metric to confirm" beside it — never as a zero, never as a plausible
 * placeholder (PRD §14.2). The functions below only pick keys out; nothing here computes a number.
 */
import type { BenchSummary } from "./api";

/**
 * A cluster-bootstrap interval as `bench/` writes it. Across seeds (the ablation matrix) it also
 * carries `std`, the sample std of the per-seed points, and `per_seed`.
 */
export type Interval = {
  point?: number | null;
  lo?: number | null;
  hi?: number | null;
  std?: number | null;
  per_seed?: number[];
  n?: number;
  n_undefined?: number;
  /** The count behind a rate, where the producer writes one (`baseline_fpr_real`). */
  k?: number;
};

/**
 * An exact Clopper–Pearson 95 % interval with the counts behind it (`recall_cp`). It carries no
 * point estimate: a perfect `k` of `n` is shown as the counts, never as "100%".
 */
export type ExactInterval = { lo?: number | null; hi?: number | null; k?: number; n?: number };

/** `sieve.eval.metrics.FpReduction`, as `bench.report_final` writes it (M2, D7). */
export type FpReduction = {
  relative?: number | null;
  relative_lo?: number | null;
  relative_hi?: number | null;
  absolute?: number | null;
  baseline_fpr?: number | null;
  sieve_fpr?: number | null;
  mcnemar_p?: number | null;
  mcnemar_p_clustered?: number | null;
  baseline_recall?: number | null;
  sieve_recall?: number | null;
  recall_not_lower?: boolean;
};

/** `bench.ablations.run.breakdown`: bucket → its counts and rates. */
export type Breakdown = Record<
  string,
  {
    n?: number;
    /** Poisoned tools in the bucket. Absent on older producers; 0 means recall is undefined. */
    n_poisoned?: number;
    recall_at_t_low?: number | null;
    fpr_stage1_at_t_low?: number | null;
    fpr_pipeline?: number | null;
  }
>;

/** One model's report (`bench.report_final.model_report`), the parts the page reads. */
export type ModelReport = {
  t_low?: number;
  t_high?: number;
  real_poisoned?: { n?: number; recall_stage1?: Interval; recall_pipeline?: number | null };
  /** Real tools only (test-ID): YARA's rate and how many of its false positives Sieve dismisses. */
  test_id?: {
    baseline_fpr_real?: Interval;
    fp_dismissed_real_triage?: Interval;
  };
  holdout_synthetic?: { n_poisoned?: number; recall_cp?: ExactInterval };
  /** Out-of-distribution sets: MCPTox's clean tools, flagged at `T_low`. */
  test_ood?: { mcptox_clean?: Interval };
  holdout?: {
    n?: number;
    fpr_pipeline_real?: Interval;
    fp_reduction_triage?: FpReduction | null;
    fp_reduction_standalone?: FpReduction | null;
    per_tier?: Breakdown;
    per_concealment?: Breakdown;
  };
};

/** `holdout.json` (H3) as the API serves it. */
export type HoldoutReport = {
  headline_model?: string;
  dataset_version?: string | null;
  n_holdout?: number;
  n_eval_only?: number;
  recall_interval?: Interval;
  fpr_pipeline_real?: Interval;
  fpr_stage1_real?: Interval;
  /** YARA's false-positive rate on the same real test-ID tools (26 of 105). */
  baseline_fpr_real?: Interval;
  /** Of the real tools YARA wrongly flagged, the share Sieve dismisses (holdout, n = 26). */
  fp_dismissed_real_triage?: Interval;
  /** Exact 95 % interval on the synthetic holdout's recall, with its k and n. */
  recall_cp?: ExactInterval;
  per_tier?: Breakdown;
  per_concealment?: Breakdown;
  release_gate?: { recall_point?: number; recall_lower_bound?: number };
  release_gate_passed?: boolean;
  judge?: { status?: string; decision?: string };
  headline_report?: ModelReport;
};

/** One row of `ablations.json` (H5). A deferred row carries no metric keys at all (D8). */
export type AblationRow = {
  row: number;
  name: string;
  status?: "active" | "deferred";
  /** Why a deferred row is deferred. */
  reason?: string;
  note?: string | null;
  classifier?: string;
  judge?: string;
  seeds?: number[];
  single_seed?: boolean;
  oof_recall_at_t_low?: Interval | null;
  fpr_stage1_at_t_low?: Interval | null;
  /** Real (public_repo, wild_fp) non-poisoned rows not auto-dismissed: the review load. */
  fpr_stage1_real_at_t_low?: Interval | null;
  fpr_pipeline?: Interval | null;
  macro_f1_mean?: number | null;
  macro_f1_std?: number | null;
  macro_f1_per_seed?: number[];
  minimal_pair_accuracy?: number | null;
  breakdown_of?: number[];
  /** The rows this breakdown row covers whose judge half is deferred (rows 10, 11, 14). */
  deferred_breakdown_of?: number[];
};

/** One audit block of `label_audit.json` (F3): human labels vs the ensemble's. */
export type AuditBlock = {
  n?: number;
  of?: number;
  errors?: number;
  undecided?: number;
  kappa?: number | null;
  kappa_lo?: number | null;
  kappa_hi?: number | null;
  error_rate?: number | null;
  cp_lo?: number | null;
  cp_hi?: number | null;
  cluster_lo?: number | null;
  cluster_hi?: number | null;
  gate?: number;
  ready?: boolean;
  passed?: boolean;
};

export type LabelAudit = {
  /** Report-only: human gold vs ensemble on the 104-row sealed audit (test-ID). */
  test_id?: AuditBlock;
  /** Decision-driving: human vs ensemble on 50 dev rows, gated at κ ≥ 0.70. */
  dev50?: AuditBlock;
};

/** `ensemble_agreement.json` (E5): the three labellers among themselves. */
export type EnsembleAgreement = {
  pairwise_kappa?: Record<string, { kappa?: number | null; n?: number }>;
  synthetic?: { checker_agreement_round0?: number | null };
};

/** One line of `bench/results/test_opened.log`. */
export type HoldoutLogRow = {
  git_sha: string;
  utc: string;
  rows_evaluated?: number;
  reason: string;
  models?: string[];
};

export type ConfusionMatrix = {
  labels: string[];
  counts: number[][];
  /** Cell the page calls out explicitly, as [rowIndex, colIndex]. */
  called_out?: [number, number];
};

export type Tables = {
  ablations?: AblationRow[];
  holdout?: HoldoutReport;
  label_audit?: LabelAudit;
  ensemble_agreement?: EnsembleAgreement;
  test_opened?: HoldoutLogRow[];
  confusion?: ConfusionMatrix;
};

/** The headline figures, picked out of the sealed report. */
export type Headline = {
  /**
   * Share of real tools the pipeline auto-confirms (test-ID). The judge is deferred, so this
   * counts Stage-1 auto-confirms only: tools in the review band are not in it. It is not a
   * false-positive rate for the product; the review share below is.
   */
  fpr_pipeline_real?: Interval;
  /** Stage-1 false-positive rate at `T_low` on the same rows. */
  fpr_stage1_real?: Interval;
  /** Real tools (test-ID) sent to review: Stage 1 at `T_low`. The product's FP number. */
  real_review_share?: Interval;
  /** YARA's rate on the same real tools, so the review share has something to be read against. */
  baseline_fpr_real?: Interval;
  /** Of the real tools YARA wrongly flagged, the share Sieve dismisses (holdout, real only). */
  fp_dismissed_real?: Interval;
  /** Exact lower bound on the synthetic holdout's recall, with its k and n. */
  holdout_recall_cp?: ExactInterval;
  /** Share of MCPTox's clean tools Sieve flags at `T_low`: read beside its recall. */
  mcptox_clean_fpr?: Interval;
  /**
   * Relative reduction in YARA's false positives with Sieve as a triage layer (M2), over ALL
   * holdout negatives, real and synthetic. Not a headline: show it only labelled with that.
   */
  fp_reduction?: Interval;
  /** McNemar's exact test p-value. It belongs to `fp_reduction`'s comparison, nothing else. */
  mcnemar_p?: number;
  /** Recall at `T_low` on holdout-synthetic: the release gate's number. */
  holdout_recall?: Interval;
  /** Stage-1 recall on the evaluation-only real poisoned slice (MCPTox, M13). */
  real_poisoned_recall?: Interval;
  release_gate_passed?: boolean;
};

export type BreakdownRow = {
  bucket: string;
  recall?: number;
  fpr?: number;
  n?: number;
  /** 0 means the bucket holds no attacks, so `recall` is undefined rather than zero. */
  n_poisoned?: number;
};

export type MinimalPairRow = { row: number; name: string; m11: number };

export function tablesOf(summary: BenchSummary | undefined): Tables {
  return (summary?.tables ?? {}) as Tables;
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function present<T extends object>(value: T | null | undefined): value is T {
  return typeof value === "object" && value !== null;
}

/** Only the keys the report actually carries; an absent figure stays absent. */
export function headlineOf(summary: BenchSummary | undefined): Headline {
  const report = tablesOf(summary).holdout;
  if (!present(report)) return {};
  const own = report.headline_report;
  const reduction = own?.holdout?.fp_reduction_triage;
  const headline: Headline = {};
  if (present(report.fpr_pipeline_real)) headline.fpr_pipeline_real = report.fpr_pipeline_real;
  if (present(report.fpr_stage1_real)) {
    headline.fpr_stage1_real = report.fpr_stage1_real;
    headline.real_review_share = report.fpr_stage1_real;
  }
  if (present(report.baseline_fpr_real)) headline.baseline_fpr_real = report.baseline_fpr_real;
  if (present(report.fp_dismissed_real_triage)) {
    headline.fp_dismissed_real = report.fp_dismissed_real_triage;
  }
  if (present(report.recall_cp)) headline.holdout_recall_cp = report.recall_cp;
  const mcptoxClean = own?.test_ood?.mcptox_clean;
  if (present(mcptoxClean)) headline.mcptox_clean_fpr = mcptoxClean;
  if (present(report.recall_interval)) headline.holdout_recall = report.recall_interval;
  const realPoisoned = own?.real_poisoned?.recall_stage1;
  if (present(realPoisoned)) headline.real_poisoned_recall = realPoisoned;
  if (present(reduction)) {
    headline.fp_reduction = {
      point: reduction.relative,
      lo: reduction.relative_lo,
      hi: reduction.relative_hi,
    };
    if (isNumber(reduction.mcnemar_p)) headline.mcnemar_p = reduction.mcnemar_p;
  }
  if (typeof report.release_gate_passed === "boolean") {
    headline.release_gate_passed = report.release_gate_passed;
  }
  return headline;
}

export function ablationsOf(summary: BenchSummary | undefined): AblationRow[] {
  const rows = tablesOf(summary).ablations;
  return Array.isArray(rows) ? rows.filter((r) => present(r) && isNumber(r.row)) : [];
}

/**
 * M11 off the scored rows. Breakdown rows (row 14) re-read the same fits, so they are left out;
 * the judge rows they would have covered are returned as `deferred` so the page can say so.
 */
export function minimalPairsOf(summary: BenchSummary | undefined): {
  rows: MinimalPairRow[];
  deferred: number[];
} {
  const ablations = ablationsOf(summary);
  const rows = ablations
    .filter((r) => isNumber(r.minimal_pair_accuracy) && (r.breakdown_of ?? []).length === 0)
    .map((r) => ({ row: r.row, name: r.name, m11: r.minimal_pair_accuracy as number }));
  const deferred = new Set<number>();
  for (const r of ablations) {
    if (isNumber(r.minimal_pair_accuracy)) {
      for (const id of r.deferred_breakdown_of ?? []) deferred.add(id);
    }
  }
  return { rows, deferred: [...deferred].sort((a, b) => a - b) };
}

function breakdownRows(breakdown: Breakdown | undefined): BreakdownRow[] {
  if (!present(breakdown)) return [];
  return Object.entries(breakdown).map(([bucket, cell]) => ({
    bucket,
    recall: isNumber(cell.recall_at_t_low) ? cell.recall_at_t_low : undefined,
    fpr: isNumber(cell.fpr_pipeline) ? cell.fpr_pipeline : undefined,
    n: isNumber(cell.n) ? cell.n : undefined,
    n_poisoned: isNumber(cell.n_poisoned) ? cell.n_poisoned : undefined,
  }));
}

/** The headline model's holdout breakdown by difficulty tier (H3). */
export function perTierOf(summary: BenchSummary | undefined): BreakdownRow[] {
  return breakdownRows(tablesOf(summary).holdout?.per_tier);
}

/** The headline model's holdout breakdown by concealment mechanism (H3). */
export function perConcealmentOf(summary: BenchSummary | undefined): BreakdownRow[] {
  return breakdownRows(tablesOf(summary).holdout?.per_concealment);
}

export function labelAuditOf(summary: BenchSummary | undefined): LabelAudit {
  const audit = tablesOf(summary).label_audit;
  return present(audit) ? audit : {};
}

export function ensembleAgreementOf(
  summary: BenchSummary | undefined,
): EnsembleAgreement | undefined {
  const agreement = tablesOf(summary).ensemble_agreement;
  return present(agreement) ? agreement : undefined;
}

export function holdoutLogOf(summary: BenchSummary | undefined): HoldoutLogRow[] {
  const log = tablesOf(summary).test_opened;
  return Array.isArray(log) ? log.filter(present) : [];
}

/**
 * True while `make bench` has not produced numbers for this deployment: the API clears it as
 * soon as the ablation matrix or the sealed report exists.
 */
export function isProvisional(summary: BenchSummary | undefined): boolean {
  return summary?.provisional !== false;
}

/** The one string the app is allowed to show where a number has not been measured. */
export const NO_NUMBER = "—";
export const NO_NUMBER_LABEL = "metric to confirm";

export function formatRatio(value: number | null | undefined, digits = 2): string {
  return isNumber(value) ? value.toFixed(digits) : NO_NUMBER;
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  return isNumber(value) ? `${(value * 100).toFixed(digits)}%` : NO_NUMBER;
}

/** "0.31 [0.22, 0.42]" — the point estimate first, then the interval, or `—`. */
export function formatInterval(interval: Interval | null | undefined, digits = 2): string {
  if (!interval || !isNumber(interval.point)) return NO_NUMBER;
  const point = interval.point.toFixed(digits);
  if (!isNumber(interval.lo) || !isNumber(interval.hi)) return point;
  return `${point} [${interval.lo.toFixed(digits)}, ${interval.hi.toFixed(digits)}]`;
}

/** "41/41" from a count and its total, or `—` when either is missing. Never divides. */
export function formatCount(
  counts: { k?: number | null; n?: number | null } | null | undefined,
): string {
  return counts && isNumber(counts.k) && isNumber(counts.n) ? `${counts.k}/${counts.n}` : NO_NUMBER;
}

/**
 * Like `formatInterval`, but a zero-width interval prints its point alone. A bootstrap over a
 * rate of exactly 0 or 1 has no spread to report, and `1.00 [1.00, 1.00]` reads as a bound.
 */
export function formatRate(interval: Interval | null | undefined, digits = 2): string {
  if (interval && isNumber(interval.lo) && isNumber(interval.hi) && interval.lo === interval.hi) {
    return formatRatio(interval.point, digits);
  }
  return formatInterval(interval, digits);
}

/** "[1.1%, 9.6%]", or `—` when either bound is missing. */
export function formatPercentRange(
  lo: number | null | undefined,
  hi: number | null | undefined,
  digits = 1,
): string {
  return isNumber(lo) && isNumber(hi)
    ? `[${formatPercent(lo, digits)}, ${formatPercent(hi, digits)}]`
    : NO_NUMBER;
}

/** "0.86 ± 0.01" across seeds, "0.86" for one value, or `—`. */
export function formatMeanStd(
  mean: number | null | undefined,
  std: number | null | undefined,
  digits = 2,
): string {
  if (!isNumber(mean)) return NO_NUMBER;
  return isNumber(std) ? `${mean.toFixed(digits)} ± ${std.toFixed(digits)}` : mean.toFixed(digits);
}
