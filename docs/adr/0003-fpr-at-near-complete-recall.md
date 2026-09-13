# ADR-003: False-positive rate at near-complete recall, and the holdout protocol

## Status

Proposed — drafted from PRD §4.3, §9.7 and §11.6 by an agent in Phase 3. The human finalises this
ADR's text.

## Context

Sieve exists because a scanner flags more than a human can read, and most of what it flags is
legitimate. The obvious headline metric for a classifier is accuracy or macro-F1, and both are
wrong here for the same reason: they let a model trade away true positives to look better. In
security, a missed poisoned tool is not one unit of error; it is the failure of the product.

There is a second, subtler problem. A benchmark this size can be reported in many ways, and
whichever way is chosen after seeing the numbers is not evidence. The corpus is small, servers are
clustered (many tools per server), and part of it is synthetic, so a single train/test split and
an unqualified point estimate would be a story rather than a result.

## Decision

**The primary metric is `FPR_pipeline` on the real subset, at a fixed, near-complete recall.**

- M1: the share of real (`public_repo`, `wild_fp`) non-poisoned holdout rows whose final verdict
  is `confirmed`, at the frozen thresholds with the judge enabled. Target at most 20%, and it
  counts only at holdout poisoned recall of at least 0.95 (point) and 0.90 (lower bound).
- M2 is reported the same way: relative reduction against the scanner baseline on the **real**
  subset, with a paired cluster bootstrap and McNemar's exact test. The synthetic-inclusive figure
  is reported second and labelled as easier.
- Every published number carries a 95% cluster-bootstrap interval that resamples **servers**, not
  rows (1,000 resamples), because tools within a server are not independent.

**The evaluation protocol is fixed before the numbers exist.**

- Every decision (architecture selection, calibration, thresholds, ablations, error analysis) uses
  pooled out-of-fold predictions from a 5-fold grouped CV over the development servers.
- The holdout (15% of servers) and the `real_poisoned` slice are opened **once** per published
  version, by `bench/eval_final.py` under `SIEVE_FINAL=1`, which appends a line to an append-only
  log before it reads anything. After it runs, no code, prompt, serialisation or synthesis change
  may follow for that version.
- The architecture is chosen by a pre-registered rule (`sieve/model/select.py`), not by taste
  after the fact.

## Consequences

- Improvements are only publishable at the fixed operating point. A number reported at any other
  point is not a result, which rules out the usual way of making a model look better.
- The holdout can be opened exactly once, so a mistake there costs a version. `bench/eval_final.py`
  logs before it reads, and `tests/bench/test_eval_final.py::test_nothing_else_in_the_repository_opens_the_holdout`
  greps `sieve/` and `bench/` to keep the rule true as the repository grows.
- Intervals will be wide. The holdout is a small number of servers and a cluster bootstrap over
  few clusters is honest about that; `bench/eval_final.py` writes the limitation into the report
  rather than leaving it for a human to remember.
- Reporting the real subset as the headline makes the project's own synthetic data unable to
  flatter it, which is the point (PRD §1.4).
- M1 as stated is unreachable without a recall gate, so the release gate lives in code
  (`RELEASE_GATE` in `bench/eval_final.py`) and the README's generated block prints whether the
  run passed it.
