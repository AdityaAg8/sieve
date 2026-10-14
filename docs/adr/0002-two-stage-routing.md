# ADR-002: Two-stage routing with an abstain band

## Status

Accepted, 2026-10-14.

## Context

Sieve has to answer one question per tool definition, cheaply enough to run over every tool a
scanner flagged and well enough that a security engineer will trust the dismissals. Two obvious
shapes were available:

- **An encoder alone.** A fine-tuned classifier is fast (tens of milliseconds on CPU), free per
  call, and reproducible offline, but it produces a probability and no reason. A dismissal with no
  quotable evidence is not something a reviewer can check.
- **An LLM judge alone.** A frontier model reads the tool the way a human would and can quote the
  span it objects to, but it costs money and latency on every single tool, cannot be replayed
  without a recorded transcript, and puts attacker-written text in front of a model on every call.

The second shape is also the expensive one exactly where it is least needed: most tool definitions
are obviously fine or obviously poisoned, and the argument only happens in the middle.

## Decision

Route on calibrated confidence, with an abstain band between two thresholds that come from the
model artefact and are never hardcoded (`sieve_config.json`):

| Stage-1 `p(poisoned)` | Verdict | `stage` |
|---|---|---|
| `>= t_high` | `confirmed` | `classifier` |
| `< t_low` | `dismissed` | `classifier` |
| in `[t_low, t_high)` | the judge decides | `judge` |

The band is the only path to Stage 2. Everything that cannot reach a judge degrades to
`needs_review`, never to `dismissed`: the judge disabled by configuration gives
`stage = classifier`, a judge failure gives `stage = judge_failed`, and an exhausted daily budget
gives `stage = judge_budget_exhausted`. The judge must return a structured
verdict with an evidence quote, so a Stage-2 answer that cannot point at text is not a verdict.

Ablation rows 6, 7 and 8 of `bench/ablations.yaml` are meant to measure what the band buys:
classifier plus Haiku, classifier plus Sonnet, and the judge alone on every row, with cost and
latency beside them. They were never run. The judge is deferred to v1.1 (ADR-009), so those rows
are marked `deferred` and carry no numbers.

## Consequences

- The routing rate is a first-class metric (target at most 25% of rows) and the cost model is a
  direct function of it. Widening the band buys accuracy with money and latency, and the
  ablation table has to show the trade rather than assert it.
- Every failure mode of Stage 2 costs a human a look instead of hiding a finding. That is the only
  safe direction for a security tool to fail in, and it is why the exhausted-budget path is a
  routing decision rather than an exception.
- Two thresholds have to be calibrated, not picked: `sieve/model/calibrate.py` writes them from
  pooled out-of-fold predictions and `thresholds.load` raises if either is null. On separable
  data the band can collapse (`t_high` onto `t_low`), which the code reports rather than
  inverting.
- The system has two models to version. `model_version` and the judge model are both recorded on
  every verdict, because a verdict is only reproducible if you know which pair produced it.
- Attacker-written text reaches an LLM on the band path, which is what ADR-002 buys and what
  `THREAT_MODEL.md` rows 1 and 2 exist to bound.
