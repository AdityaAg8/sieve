# ADR-005: ONNX Runtime with dynamic int8 quantisation for serving

## Status

Proposed — drafted from PRD §7.2, §11.7 and §27 by an agent in Phase 3, against
`sieve/model/export.py` and `sieve/model/infer.py` as built. No latency has been measured on this
machine. The human finalises this ADR's text.

**Amended by ADR-009 (2026-10-04).** The revisit trigger below fired on the v1 artefact. Dynamic
int8 failed parity at max |Δp| 0.77, and per-channel MatMul-only int8 at 0.92. The fp32 ONNX graph
matched PyTorch to within 1.4e-6, so v1 serves fp32 ONNX (`make export` passes `--no-quantize`).
The parity figure is now `onnx_parity`, with `onnx_graph` naming the graph it measured.

## Decision drivers

- Stage 1 runs on every tool of every scan, on CPU, on a small cloud instance (2 vCPU).
- The published latency target (M5) is provisional: p95 at most 200 ms for one tool at the
  configured maximum length.
- Whatever is served has to produce the same probabilities the thresholds were calibrated on, or
  the thresholds mean nothing.

## Context

The trained artefact is a PyTorch encoder (DeBERTa-v3-base or ModernBERT-base, ~150 to 185M
parameters). Serving it in PyTorch eager mode on CPU is the zero-effort option and is slow enough
that the abstain band's latency budget would be spent before the judge is even asked. GPU serving
is not on the table for a portfolio deployment.

## Decision

Export the selected artefact to ONNX and apply dynamic int8 quantisation
(`sieve/model/export.py`), serve it with ONNX Runtime, and record `p95_ms`, `p50_ms` and an
`onnx_int8_parity` figure into the same `sieve_config.json` that carries the thresholds.
`SIEVE_USE_ONNX` defaults to true; the PyTorch path stays loadable and
`tests/model/test_infer.py::test_torch_classifier_agrees_with_the_onnx_one` pins the two together.

Rejected: PyTorch eager (too slow on CPU for the band's budget), and static/QAT quantisation (a
calibration set and a training loop for a gain nobody has shown is needed here).

## Consequences

- Parity is a release gate, not a hope. PRD §27 names the revisit trigger: a parity delta above
  0.01 in probability sends the decision back.
- Latency is machine-dependent and is published as a measured number (M5, M6c), excluded from the
  byte-identical reproducibility gate (M6a) precisely because it cannot be reproduced on a
  different box.
- Calibration lives with the artefact, so the exported model, its temperature and its thresholds
  travel together. A partially updated artefact refuses to load rather than serving uncalibrated
  scores (`tests/model/test_infer.py::test_load_classifier_raises_when_the_artefact_was_never_calibrated`).
- Quantisation is a second place the model can change silently. It is covered by the parity test
  and by the ablation matrix's `p95_ms` column, not by inspection.
- No number in this ADR has been measured yet: no GPU exists on the development machine, so
  nothing has been trained, exported or timed. Every figure above is a target from PRD §4.3.
