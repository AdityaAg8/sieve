"""ONNX export, dynamic int8 quantisation, parity against PyTorch, and CPU latency.

Serving is CPU-only, so the published artefact is the quantised graph. Quantisation is a
behaviour change, not a packaging step: `parity` measures what it cost and the number goes into
`sieve_config.json` beside the thresholds, so nobody has to take it on trust (PRD §11.7).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

log = logging.getLogger("sieve.model.export")

FP32_ONNX = "model.onnx"
INT8_ONNX = "model.int8.onnx"
CONFIG_FILE = "sieve_config.json"
DEFAULT_MAX_LENGTH = 512
#: Max |Δp| between PyTorch and the published graph, on the dev set. Enforced (H6): above it
#: the export fails rather than shipping a graph that disagrees with the model it claims to be.
PARITY_TOLERANCE = 0.01
PARITY_BATCH = 32
PARITY_REPORT = "export_parity.json"


class ParityError(RuntimeError):
    """The exported graph disagrees with PyTorch by more than `PARITY_TOLERANCE`."""


_COPIED = ("sieve_config.json", "metrics.json")


def _max_length(model_dir: Path) -> int:
    path = model_dir / CONFIG_FILE
    if not path.exists():
        return DEFAULT_MAX_LENGTH
    config = json.loads(path.read_text(encoding="utf-8"))
    return int(config.get("max_length") or DEFAULT_MAX_LENGTH)


def _temperature(model_dir: Path) -> float:
    """The artefact's temperature, or 1.0 when it has not been calibrated yet.

    Unlike serving, parity has no operating point to get wrong: it compares two implementations
    of the same model, and a shared scalar divides out of both sides. An export therefore does
    not require a calibrated artefact, while `infer.load_classifier` still does.
    """
    path = model_dir / CONFIG_FILE
    if not path.exists():
        return 1.0
    value = json.loads(path.read_text(encoding="utf-8")).get("temperature")
    return 1.0 if value is None else float(value)


def _softmax(logits: npt.NDArray[Any], temperature: float) -> npt.NDArray[np.float64]:
    scaled = np.asarray(logits, dtype=np.float64) / float(temperature)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return np.asarray(exponentiated / exponentiated.sum(axis=1, keepdims=True), dtype=np.float64)


def export(model_dir: Path, out_dir: Path, *, quantize: bool = True) -> Path:
    """Write `model.onnx` (and `model.int8.onnx`) plus the tokenizer and `sieve_config.json`."""
    from optimum.onnxruntime import (  # type: ignore[import-untyped]
        ORTModelForSequenceClassification,
    )
    from transformers import AutoTokenizer

    out_dir.mkdir(parents=True, exist_ok=True)
    ort_model = ORTModelForSequenceClassification.from_pretrained(str(model_dir), export=True)
    ort_model.save_pretrained(str(out_dir))
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))  # type: ignore[no-untyped-call]
    tokenizer.save_pretrained(str(out_dir))

    fp32 = out_dir / FP32_ONNX
    if not fp32.exists():  # pragma: no cover - optimum names the graph itself
        raise FileNotFoundError(f"optimum wrote no {FP32_ONNX} into {out_dir}")

    if not quantize:
        # `infer._onnx_graph` prefers int8: a graph left from an earlier quantised export would be
        # served instead of the fp32 one this export checks (Z2 review).
        (out_dir / INT8_ONNX).unlink(missing_ok=True)
    if quantize:
        from onnxruntime.quantization import (  # type: ignore[import-untyped]
            QuantType,
            quantize_dynamic,
        )

        quantize_dynamic(fp32, out_dir / INT8_ONNX, weight_type=QuantType.QInt8)
        log.info(
            "int8 %.2f MB from fp32 %.2f MB",
            (out_dir / INT8_ONNX).stat().st_size / 1e6,
            fp32.stat().st_size / 1e6,
        )

    for name in _COPIED:
        source, target = model_dir / name, out_dir / name
        # Exporting into the artefact itself: the file is already where it belongs.
        if source.exists() and source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    return out_dir


def _onnx_probs(
    onnx_path: Path, texts: Sequence[str], *, model_dir: Path, threads: int = 2
) -> npt.NDArray[np.float64]:
    import onnxruntime as ort  # type: ignore[import-untyped]
    from transformers import AutoTokenizer

    if not onnx_path.exists():
        raise FileNotFoundError(onnx_path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    tokenizer = AutoTokenizer.from_pretrained(str(onnx_path.parent))  # type: ignore[no-untyped-call]
    wanted = {i.name for i in session.get_inputs()}
    blocks = []
    items = list(texts)
    for start in range(0, len(items), PARITY_BATCH):
        encoded = tokenizer(
            items[start : start + PARITY_BATCH],
            truncation=True,
            max_length=_max_length(model_dir),
            padding=True,
            return_tensors="np",
        )
        feed = {k: v.astype(np.int64) for k, v in encoded.items() if k in wanted}
        blocks.append(_softmax(session.run(None, feed)[0], _temperature(model_dir)))
    return np.concatenate(blocks, axis=0) if blocks else np.zeros((0, 3))


def _torch_probs(model_dir: Path, texts: Sequence[str]) -> npt.NDArray[np.float64]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))  # type: ignore[no-untyped-call]
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).eval()
    blocks = []
    items = list(texts)
    for start in range(0, len(items), PARITY_BATCH):
        encoded = tokenizer(
            items[start : start + PARITY_BATCH],
            truncation=True,
            max_length=_max_length(model_dir),
            padding=True,
            return_tensors="pt",
        )
        with torch.inference_mode():
            logits = model(**encoded).logits.float().numpy()
        blocks.append(_softmax(logits, _temperature(model_dir)))
    return np.concatenate(blocks, axis=0) if blocks else np.zeros((0, 3))


def _threshold(model_dir: Path, name: str) -> float | None:
    path = model_dir / CONFIG_FILE
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get(name)
    return None if value is None else float(value)


def parity_report(model_dir: Path, onnx_path: Path, texts: Sequence[str]) -> dict[str, Any]:
    """Max |Δp| between PyTorch and the graph, plus the verdict flips it causes: rows on the
    other side of `T_low` or `T_high` (poisoned column) under the graph than under PyTorch."""
    if not onnx_path.exists():
        raise FileNotFoundError(onnx_path)
    torch_probs = _torch_probs(model_dir, texts)
    onnx_probs = _onnx_probs(onnx_path, texts, model_dir=model_dir)
    delta = float(np.max(np.abs(torch_probs - onnx_probs))) if len(texts) else 0.0
    report: dict[str, Any] = {
        "graph": onnx_path.name,
        "n": len(texts),
        "max_abs_delta": delta,
        "tolerance": PARITY_TOLERANCE,
        "passed": delta <= PARITY_TOLERANCE,
    }
    for name in ("t_low", "t_high"):
        threshold = _threshold(model_dir, name)
        report[f"flips_{name}"] = (
            None
            if threshold is None
            else int(np.sum((torch_probs[:, 2] >= threshold) != (onnx_probs[:, 2] >= threshold)))
        )
    return report


def parity(
    model_dir: Path, onnx_path: Path, texts: Sequence[str], *, tol: float = PARITY_TOLERANCE
) -> float:
    """Max |Δp| between the PyTorch and ONNX probabilities. Logs a warning above `tol`."""
    delta = float(parity_report(model_dir, onnx_path, texts)["max_abs_delta"])
    if delta > tol:
        log.warning("ONNX parity %.4f exceeds the %.4f tolerance for %s", delta, tol, onnx_path)
    return delta


def parity_texts(corpus: Path, limit: int = 0) -> list[str]:
    """The dev set's texts (H6): parity is measured where the model is used, not on a toy."""
    from sieve.model.data import load_corpus
    from sieve.serialise import serialise

    source = corpus if corpus.exists() else Path("fixtures/corpus/mini-50.jsonl")
    if source != corpus:
        log.warning("%s is missing; measuring parity on the mini-50 fixture instead", corpus)
    records = load_corpus(source)
    # Dev rows, else an unsplit corpus (the fixture); never a sealed row (Z2 review).
    dev = [r for r in records if r.split == "dev"] or [r for r in records if r.split is None]
    texts = [serialise(r.to_tool_record()) for r in dev]
    return texts[:limit] if limit else texts


def bench_latency(onnx_path: Path, text: str, *, n: int = 50, threads: int = 2) -> dict[str, float]:
    """Single-tool p50/p95 in milliseconds on `threads` CPU threads (PRD M5)."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    tokenizer = AutoTokenizer.from_pretrained(str(onnx_path.parent))  # type: ignore[no-untyped-call]
    wanted = {i.name for i in session.get_inputs()}
    # The artefact's own length (a 1,024-token model is timed at 1,024), not the default.
    encoded = tokenizer(
        [text], truncation=True, max_length=_max_length(onnx_path.parent), return_tensors="np"
    )
    feed = {k: v.astype(np.int64) for k, v in encoded.items() if k in wanted}

    session.run(None, feed)  # warm the first allocation out of the sample
    samples: list[float] = []
    for _ in range(n):
        started = time.perf_counter()
        session.run(None, feed)
        samples.append((time.perf_counter() - started) * 1000.0)
    return {
        "p50_ms": float(np.percentile(samples, 50)),
        "p95_ms": float(np.percentile(samples, 95)),
    }


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.export")
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-quantize", action="store_true")
    parser.add_argument("--latency", action="store_true")
    parser.add_argument("--latency-n", type=int, default=50)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--parity-texts", type=int, default=0, help="0: the whole dev set")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    out_dir = ns.out if ns.out is not None else ns.model_dir / "onnx"
    export(ns.model_dir, out_dir, quantize=not ns.no_quantize)
    graph = out_dir / (FP32_ONNX if ns.no_quantize else INT8_ONNX)

    texts = parity_texts(ns.corpus, ns.parity_texts)
    report = parity_report(ns.model_dir, graph, texts)
    config_path = ns.model_dir / CONFIG_FILE
    if config_path.exists():
        report["model_version"] = json.loads(config_path.read_text(encoding="utf-8")).get(
            "model_version"
        )
    ns.results_dir.mkdir(parents=True, exist_ok=True)
    (ns.results_dir / PARITY_REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if not report["passed"]:
        # A refused graph must not stay where `infer._onnx_graph` finds it, or the sealed
        # evaluation and the server would load it anyway (Z2 review). Both graphs go: the fp32
        # one beside a refused int8 was never checked either.
        for name in (INT8_ONNX, FP32_ONNX):
            (out_dir / name).unlink(missing_ok=True)
        raise ParityError(
            f"{graph}: max |Δp| {report['max_abs_delta']:.4f} > {PARITY_TOLERANCE} on "
            f"{report['n']} dev rows; the export is refused"
        )
    measured: dict[str, Any] = {"onnx_int8_parity": report["max_abs_delta"]}
    if ns.latency:
        measured.update(bench_latency(graph, texts[0], n=ns.latency_n, threads=ns.threads))

    # Written into BOTH configs. `sieve.model.select` and the ablation matrix read the source
    # artefact's `sieve_config.json`, not the exported subdirectory's, so writing only the
    # latter left the latency tie-break and the `p95_ms` column permanently null.
    for target in (out_dir / CONFIG_FILE, ns.model_dir / CONFIG_FILE):
        existing: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        existing.update(measured)
        target.write_text(
            json.dumps(existing, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return Path(out_dir)


__all__ = [
    "FP32_ONNX",
    "INT8_ONNX",
    "PARITY_TOLERANCE",
    "ParityError",
    "bench_latency",
    "export",
    "main",
    "parity",
    "parity_report",
]

if __name__ == "__main__":  # pragma: no cover
    main()
