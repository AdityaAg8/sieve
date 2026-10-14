---
library_name: transformers
pipeline_tag: text-classification
tags:
  - mcp
  - tool-poisoning
  - security
  - triage
---

# {{model_version}}

Stage 1 of **Sieve**, a triage layer for MCP tool-poisoning scanner findings. It is a
three-class encoder over a field-marked serialisation of an MCP tool definition
(`benign` / `imperative_benign` / `poisoned`), calibrated so its `poisoned` probability can be
thresholded into auto-dismiss, route-to-judge and auto-confirm bands.

Sieve is **not a scanner**. It writes no detection rules and finds nothing on its own; it reads
what a scanner already flagged and decides what a human should look at.

- Base model: `{{base_model}}`
- Trained from git SHA: `{{git_sha}}`
- Dataset: SieveBench `{{dataset_version}}`
- Licensing: {{licences}}

## Training command

```bash
{{training_command}}
```

## Pre-registered architecture selection

The rule was written down before any number existed: **the architecture with the higher mean
pooled out-of-fold macro-F1 across seeds wins**; ties inside 0.005 break toward the lower p95
latency, and a full tie toward DeBERTa-v3 as the declared default.

- Winner: **{{selection_winner}}**
- Margin: {{selection_margin}}
- Rule as recorded in `selection.json`: {{selection_rule}}

{{selection_table}}

## Operating point

Thresholds live in this artefact's `sieve_config.json` and are never hardcoded by a caller.

| | |
|---|---|
| `T_low` (auto-dismiss below) | {{t_low}} |
| `T_high` (auto-confirm at or above) | {{t_high}} |
| `T_low` chosen on four folds, applied to the fifth (its real variability) | {{crossfit_t_low}} |
| `T_low` stability across bootstrap seeds (Monte-Carlo noise only) | {{t_low_spread}} |
| Temperature | {{temperature}} |
| ECE before / after temperature scaling | {{ece_before}} / {{ece_after}} |
| Cross-fitted by fold: recall at `T_low` / ECE after (top-label, poisoned column) | {{crossfit}} |
| Judge routing rate | {{routing_rate}} |

`T_low` is the highest threshold whose **cluster-bootstrap (by server) 95% lower bound** of
`poisoned` recall clears the target, not the lowest positive score, which is a one-row
statistic. With about 245 development positives that rule tolerates one or two misses, so in
practice `T_low` sits on the score of one hard synthetic attack, and a single row moves the
review load by tens of points. The recall-vs-threshold and FPR-vs-recall curves are published in
`bench/results/curves.json`, and a cross-fitted post-hoc table of other rules in
`bench/results/operating_points.json`, so the operating point is visibly a choice on a curve.

Recall at `T_low` measured on the rows that chose `T_low` clears the target by construction, so
the cross-fitted row is the honest one: each fold is scored with a temperature and a `T_low`
fitted on the other four folds. ECE is top-label over 15 equal-width bins; the reliability
diagrams, in-sample and cross-fitted, are in `curves.json` under `reliability`.

## Metrics

Every interval is a 95% cluster bootstrap resampling **servers**, 1,000 resamples, except where
it says exact (Clopper-Pearson). Development numbers are pooled out-of-fold over the 5-fold
grouped CV; holdout numbers were produced by a single sealed run (see the log line below).

**Labels:** {{labels_status}}. Every figure below carries that caveat.

### Development (pooled out-of-fold)

| Metric | Value |
|---|---|
| Macro-F1 (mean ± std across seeds) | {{macro_f1}} |
| Real tools sent to review (`FPR_stage1@T_low`, real subset) | {{fpr_stage1_real_at_t_low}} |
| Same, per seed | {{per_seed_review}} |
| Scanner false positives dismissed (real subset) | {{fp_dismissed_real_triage}} |
| `FPR_stage1@T_low`, all non-poisoned rows (synthetic included) | {{fpr_stage1_at_t_low}} |
| `FPR_pipeline` (auto-confirmed only) | {{fpr_pipeline}} |
| Minimal-pair accuracy | {{minimal_pair_accuracy}} |

Both FPR definitions are reported because they answer different questions.
`FPR_stage1@T_low` counts a non-poisoned row as a false positive when it was **not
auto-dismissed**; `FPR_pipeline` counts it only when the **final verdict is `confirmed`**. With
the judge deferred, every tool that is not dismissed goes to a human, so in v1 the product number
is `FPR_stage1@T_low` on real tools: the review load. `FPR_pipeline` leaves that band out and is
never a false-positive reduction on its own. Development holds no real poisoned row, so every
development recall is measured on synthetic attacks.

### Holdout (opened once)

| Metric | Value |
|---|---|
| `poisoned` recall, synthetic attacks (point / bootstrap lower bound) | {{holdout_recall}} / {{holdout_recall_lcb}} |
| Same, exact bound (the bootstrap collapses when every row is a hit) | {{holdout_recall_cp}} |
| Real tools sent to review | {{holdout_review_real}} |
| Scanner flag rate on the same real tools | {{holdout_baseline_fpr_real}} |
| Scanner false positives dismissed (real tools) | {{holdout_fp_dismissed_real}} |
| `FPR_pipeline` on the real subset (auto-confirmed only) | {{holdout_fpr_pipeline}} |
| `real_poisoned` recall at Stage 1 / after the pipeline (MCPTox) | {{real_poisoned_stage1}} / {{real_poisoned_pipeline}} |
| MCPTox clean tools flagged at `T_low` | {{mcptox_clean_fpr}} |
| Docker catalogue tools flagged at `T_low` | {{docker_fpr}} |
| Independent attack sets (val-OOD, report-only) | {{val_ood}} |

Per-tier: {{per_tier}}

Per-concealment: {{per_concealment}}

### Judge self-defence (injection suites)

Three numbers belong here, and they are reported separately because they answer three different
questions. A single "injection resistance" figure would claim more than any one of them shows:

1. the **dev gate**, `tests/test_judge_injection.py`: the suite the prompt was tuned against,
   which is a gate and must pass;
2. the **holdout**, `tests/test_judge_injection_holdout.py`: run once per version against cases
   the prompt has never seen, written to `bench/results/judge_injection_holdout.json`, and
   **reported, not gated**;
3. the **external** benchmark, whichever public set is cited, run unchanged.

No prompt change may follow a holdout run for the version it measured; if the result is bad, that
is the result and the next version is where it is addressed.

**Not yet measured.** No slot is templated above because no artefact exists to fill one from.
Until the holdout run is recorded, Sieve's injection resistance is a design claim, as
THREAT_MODEL.md's residual risk 8 also says.

### Serving

| | |
|---|---|
| ONNX parity ({{onnx_graph}}, max abs delta p vs PyTorch) | {{onnx_parity}} |
| p50 / p95 latency, one tool, 2 vCPU | {{p50_ms}} / {{p95_ms}} |

## Holdout log line

The holdout is opened once per published version, and the line is published with the model:

```json
{{holdout_log_line}}
```

A card that quotes holdout numbers without this line is not publishable.

## Intended use

- Triaging findings a scanner (Cisco MCP Scanner, Snyk, or a SARIF import) has already
  produced, inside a review workflow where a human sees anything routed to `needs_review`.
- Research and replication of the SieveBench numbers.

## Out of scope

- Scanning. It has no rules and no signatures; hand it a tool definition nobody flagged and it
  will still give you a probability, which is not the same as a detection.
- Deciding alone. The auto-confirm band exists to save review time, not to take an action.
- Anything but MCP tool definitions. It was trained on one serialisation of one object shape.

## Limitations

{{limitations}}

- Every `poisoned` training and development row is a synthetic mutation, written by two local
  LLM families (6.3% hand-written), so a recall computed over them partly measures agreement with
  those generators. Real-subset numbers are false-positive rates only: no real subset, development
  or holdout, holds a poisoned row. Real-attack recall comes from MCPTox and val-OOD alone.
- The model's scores carry a synthetic-source tell: synthetic non-poisoned rows score higher
  than real ones ({{source_probe}}; `ablations.json` `source_probe`).
- Concealment coverage is uneven. A ten-line unicode/hidden-content detector is reported as an
  ablation for exactly this reason; where it wins, the capability belongs in preprocessing.
- Everything inside a tool definition is untrusted text. This model reads it; it does not
  execute it, and neither should anything downstream.

## Citation and provenance

Generated by `python -m sieve.model.push`. Numbers come from `bench/results/` and are
regenerated by `make bench` from committed prediction artefacts; none were edited by hand.
