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
| `T_low` spread over five bootstrap refits | {{t_low_spread}} |
| Temperature | {{temperature}} |
| ECE before / after temperature scaling | {{ece_before}} / {{ece_after}} |
| Judge routing rate | {{routing_rate}} |

`T_low` is the highest threshold whose **cluster-bootstrap (by server) 95% lower bound** of
`poisoned` recall clears the target, not the lowest positive score, which is a one-row
statistic. The recall-vs-threshold and FPR-vs-recall curves are published in
`bench/results/curves.json`, so the operating point is visibly a choice on a curve.

## Metrics

Every interval is a 95% cluster bootstrap resampling **servers**, 1,000 resamples. Development
numbers are pooled out-of-fold over the 5-fold grouped CV; holdout numbers were produced by a
single sealed run (see the log line below).

### Development (pooled out-of-fold)

| Metric | Value |
|---|---|
| Macro-F1 (mean ± std across seeds) | {{macro_f1}} |
| `FPR_stage1@T_low` | {{fpr_stage1_at_t_low}} |
| `FPR_pipeline` | {{fpr_pipeline}} |
| Minimal-pair accuracy (M11) | {{minimal_pair_accuracy}} |

Both FPR definitions are reported because they answer different questions.
`FPR_stage1@T_low` counts a non-poisoned row as a false positive when it was **not
auto-dismissed**; `FPR_pipeline` counts it only when the **final verdict is `confirmed`**. The
second is the product number.

### Holdout (opened once)

| Metric | Value |
|---|---|
| `poisoned` recall (point / lower bound) | {{holdout_recall}} / {{holdout_recall_lcb}} |
| `FPR_pipeline` on the real subset | {{holdout_fpr_pipeline}} |
| `real_poisoned` recall at Stage 1 / after the pipeline | {{real_poisoned_stage1}} / {{real_poisoned_pipeline}} |

Per-tier: {{per_tier}}

Per-concealment: {{per_concealment}}

### Judge self-defence (injection suites)

Three numbers belong here, and they are reported separately because they answer three different
questions. A single "injection resistance" figure would be the claim PRD §12.5 refuses to make:

1. the **dev gate**, `tests/test_judge_injection.py`: the suite the prompt was tuned against,
   which is a gate and must pass;
2. the **holdout**, `tests/test_judge_injection_holdout.py`: run once per version against cases
   the prompt has never seen, written to `bench/results/judge_injection_holdout.json`, and
   **reported, not gated**;
3. the **external** benchmark, whichever public set is cited, run unchanged.

No prompt change may follow a holdout run for the version it measured; if the result is bad, that
is the result and the next version is where it is addressed.

**Not yet measured.** No slot is templated above because there is no artefact to fill one from:
recording the holdout is a one-shot human step (`docs/RUNBOOK.md` §3), and until it has run,
Sieve's injection resistance is a design claim, as THREAT_MODEL.md's residual risk 8 also says.
Task 11.6 fills the three in, with slots, once they exist.

### Serving

| | |
|---|---|
| ONNX int8 parity (max abs delta p vs PyTorch) | {{onnx_int8_parity}} |
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

- A large share of the `poisoned` training rows are synthetic mutations authored by an LLM, so
  a figure computed over them partly measures self-consistency. Real-subset numbers are
  reported separately and are the ones to believe.
- Concealment coverage is uneven. A ten-line unicode/hidden-content detector is reported as an
  ablation for exactly this reason; where it wins, the capability belongs in preprocessing.
- Everything inside a tool definition is untrusted text. This model reads it; it does not
  execute it, and neither should anything downstream.

## Citation and provenance

Generated by `python -m sieve.model.push`. Numbers come from `bench/results/` and are
regenerated by `make bench` from committed prediction artefacts; none were edited by hand.
