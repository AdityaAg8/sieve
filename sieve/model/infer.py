"""The serving side of Stage 1: load an artefact, return all three calibrated probabilities.

`predict_proba` returns the full `[n, 3]` block, never a collapsed poisoned score, because
`TriageResult` carries `predicted_class` and `class_probabilities` and the UI has to be able to
say *why* a dismissal is a dismissal (PRD §8.1, §14.6). Probabilities are already
temperature-scaled on the way out; a caller that scaled again would double-count.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from sieve.config import settings
from sieve.model import thresholds as th
from sieve.model.export import FP32_ONNX, INT8_ONNX
from sieve.protocols import FAKE_POISON_WORDS, Classifier, FakeClassifier, Thresholds
from sieve.types import LABELS, Label

log = logging.getLogger("sieve.model.infer")

FIXTURE_ID = "fixture"
DEFAULT_MAX_LENGTH = 512


def _check_shape(probs: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    array = np.asarray(probs, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != len(LABELS):
        raise ValueError(
            f"expected a block three columns wide in {LABELS} order, got shape {array.shape}"
        )
    return array


def predicted_class(probs: npt.NDArray[np.float64]) -> list[Label]:
    """The argmax class per row, read through `LABELS` so column order is never re-derived."""
    return [LABELS[int(i)] for i in _check_shape(probs).argmax(axis=1)]


def class_probabilities(probs: npt.NDArray[np.float64]) -> list[dict[Label, float]]:
    """One `{label: p}` mapping per row, with exactly the three labels as keys."""
    return [
        {label: float(row[index]) for index, label in enumerate(LABELS)}
        for row in _check_shape(probs)
    ]


def _softmax(logits: npt.NDArray[Any], temperature: float) -> npt.NDArray[np.float64]:
    scaled = np.asarray(logits, dtype=np.float64) / float(temperature)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return np.asarray(exponentiated / exponentiated.sum(axis=1, keepdims=True), dtype=np.float64)


def _artefact(model_dir: Path) -> tuple[str, int, Thresholds]:
    """`(model_version, max_length, thresholds)`. Raises rather than defaulting a threshold."""
    config_path = model_dir / th.CONFIG_FILE
    thresholds = th.load(model_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    version = str(config.get("model_version") or model_dir.name)
    return version, int(config.get("max_length") or DEFAULT_MAX_LENGTH), thresholds


def _onnx_graph(model_dir: Path) -> Path | None:
    for name in (INT8_ONNX, FP32_ONNX):
        if (model_dir / name).exists():
            return model_dir / name
    return None


class OnnxClassifier:
    """Satisfies `sieve.protocols.Classifier`. The published serving path (PRD §11.7)."""

    def __init__(self, model_dir: Path, *, threads: int = 2) -> None:
        import onnxruntime as ort  # type: ignore[import-untyped]
        from transformers import AutoTokenizer

        self.model_dir = Path(model_dir)
        self.model_version, self._max_length, self.thresholds = _artefact(self.model_dir)
        graph = _onnx_graph(self.model_dir)
        if graph is None:
            raise FileNotFoundError(f"no {INT8_ONNX} or {FP32_ONNX} in {self.model_dir}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(graph), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer: Any = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            str(self.model_dir)
        )

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        encoded = self._tokenizer(
            list(texts),
            truncation=True,
            max_length=self._max_length,
            padding=True,
            return_tensors="np",
        )
        feed = {k: v.astype(np.int64) for k, v in encoded.items() if k in self._inputs}
        logits = self._session.run(None, feed)[0]
        return _softmax(logits, self.thresholds.temperature)


class TorchClassifier:
    """The reference implementation. Used to measure ONNX parity and when no graph exists."""

    def __init__(self, model_dir: Path) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.model_dir = Path(model_dir)
        self.model_version, self._max_length, self.thresholds = _artefact(self.model_dir)
        self._tokenizer: Any = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            str(self.model_dir)
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir)).eval()

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        encoded = self._tokenizer(
            list(texts),
            truncation=True,
            max_length=self._max_length,
            padding=True,
            return_tensors="pt",
        )
        with self._torch.no_grad():
            logits = self._model(**encoded).logits.numpy()
        return _softmax(logits, self.thresholds.temperature)


def load_classifier(
    model_id: str,
    *,
    revision: str | None = None,
    use_onnx: bool = True,
    cache_dir: Path | None = None,
) -> Classifier:
    """Resolve `"fixture"`, then a local directory, then the Hub.

    `FakeClassifier` is imported from `sieve.protocols`, never redefined: every lane's fixture
    behaviour has to come from one object or the fixtures stop agreeing (PRD §8.2).
    """
    if model_id == FIXTURE_ID:
        return FakeClassifier()

    local = Path(model_id)
    if local.is_dir():
        directory = local
    else:
        from huggingface_hub import snapshot_download

        cache = cache_dir or Path(settings.model_cache).expanduser()
        directory = Path(snapshot_download(model_id, revision=revision, cache_dir=str(cache)))

    if use_onnx and _onnx_graph(directory) is not None:
        return OnnxClassifier(directory)
    if use_onnx:
        log.info("no ONNX graph in %s; falling back to the PyTorch model", directory)
    return TorchClassifier(directory)


__all__ = [
    "FAKE_POISON_WORDS",
    "Classifier",
    "FakeClassifier",
    "OnnxClassifier",
    "Thresholds",
    "TorchClassifier",
    "class_probabilities",
    "load_classifier",
    "predicted_class",
]
