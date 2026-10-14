# ADR-005: ONNX Runtime with fp32 serving (int8 quantisation rejected)

## Status

Accepted, 2026-10-14.

The first plan was dynamic int8 quantisation. It failed on the v1 artefact: dynamic int8 reached
max |Δp| 0.77 against PyTorch, and per-channel MatMul-only int8 reached 0.92. The fp32 ONNX graph
matched PyTorch to within 6.7e-6 over all 1,010 development rows with no threshold flips
(`bench/results/export_parity.json`). v1 therefore serves fp32 ONNX, and `make export` passes
`--no-quantize`. ADR-009 has the measurements.

## Decision drivers

- Stage 1 runs on every tool of every scan, on CPU, on a small cloud instance (2 vCPU).
- The published latency target is provisional: p95 at most 200 ms for one tool at the
  configured maximum length.
- Whatever is served has to produce the same probabilities the thresholds were calibrated on, or
  the thresholds mean nothing.

## Context

The trained artefact is a PyTorch encoder (DeBERTa-v3-base or ModernBERT-base, ~150 to 185M
parameters). Serving it in PyTorch eager mode on CPU is the zero-effort option and is slow enough
that the abstain band's latency budget would be spent before the judge is even asked. GPU serving
is not on the table for a portfolio deployment.

## Decision

Export the selected artefact to ONNX at fp32 (`sieve/model/export.py`), serve it with ONNX
Runtime, and record `p95_ms`, `p50_ms` and an `onnx_parity` figure (with `onnx_graph` naming the
graph it measured) in the same `sieve_config.json` that carries the thresholds.
`SIEVE_USE_ONNX` defaults to true; the PyTorch path stays loadable and
`tests/model/test_infer.py::test_torch_classifier_agrees_with_the_onnx_one` pins the two together.

Rejected: PyTorch eager (too slow on CPU for the band's budget), int8 quantisation (dynamic and
per-channel both failed parity, see above), and static or quantisation-aware training (a
calibration set and a training loop for a gain nobody has shown is needed here).

## Consequences

- Parity is a release gate. A parity delta above 0.01 in probability sends the decision back, and
  that is what happened to int8.
- Latency is machine-dependent and is published as a measured number and excluded from the
  byte-identical reproducibility gate precisely because it cannot be reproduced on a
  different box.
- Calibration lives with the artefact, so the exported model, its temperature and its thresholds
  travel together. A partially updated artefact refuses to load rather than serving uncalibrated
  scores (`tests/model/test_infer.py::test_load_classifier_raises_when_the_artefact_was_never_calibrated`).
- fp32 is slow on CPU. One 512-token tool on 2 threads takes roughly a second, several times the
  200 ms target, and the README reports that miss. Shorter serialisation, distillation, or
  quantisation that keeps the attention layers in fp32 are the v1.1 options.
