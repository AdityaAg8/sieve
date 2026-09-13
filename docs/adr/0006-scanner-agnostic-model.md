# ADR-006: A scanner-agnostic model by default

## Status

Proposed — drafted from PRD §7.2, §11.2 and §11.5 by an agent in Phase 3. The human finalises this
ADR's text.

## Context

Sieve sits behind a scanner, so the scanner's own output is available as a feature: which rules
fired, at what severity, how many. Feeding that to Stage 1 is free accuracy on the corpus, because
the corpus was assembled partly from what a scanner flagged.

It is also a trap. A model that reads the scanner's verdict learns the scanner, and three things
follow: it cannot triage a tool the scanner never flagged, its numbers are not comparable against
the scanner it is being compared to, and it silently inherits every bias of one vendor's rule set
at one version. Sieve is meant to be the layer that disagrees with the scanner.

## Decision

The default serialisation (`sieve/serialise.py`) contains only what the MCP server itself
published: `[NAME]`, `[TITLE]`, `[DESC]`, `[PARAM]`, `[ANNOT]`, `[SIBLINGS]` and `[OUTPUT]`. No
scanner finding, rule id, severity or count is a model input in the shipped configuration.

Scanner features are kept as an **ablation**, not a default: row 9 of `bench/ablations.yaml` trains
its own artefact with `scanner_features: true` and is reported beside row 4 so the contribution is
visible and arguable. The adapters and the corpus keep the findings either way, because the
baseline comparison and the false-positive-cause taxonomy need them.

## Consequences

- Sieve can triage any tool definition, including one no scanner flagged, and the same model runs
  from the CLI over a plain `tools/list` dump.
- The comparison against the baseline stays meaningful: the model is not reading the baseline's
  answer before disagreeing with it.
- If row 9 shows a large gain, that is a real finding and it belongs in the write-up as one, with
  the coupling it implies stated plainly. Publishing it as the default would trade the product's
  independence for benchmark points.
- Ingest paths that carry no description (`--from cisco-yara`, `--from sarif`) are therefore weak
  by construction: the classifier sees only a tool name. That is a documented limitation of
  ingesting someone else's report, not a reason to add the report's findings as features.
- One more thing has to be measured rather than assumed: the leakage probe runs within the
  `imperative_benign` stratum, so it tests for style leakage with the label held constant.
