from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sieve.model.infer import (
    OnnxClassifier,
    TorchClassifier,
    class_probabilities,
    load_classifier,
    predicted_class,
)
from sieve.model.thresholds import ThresholdsMissing
from sieve.protocols import Classifier, FakeClassifier
from sieve.types import LABELS
from tests.model.artefacts import CALIBRATION, build_calibrated_tiny

# Targeted, not blanket: the project turns warnings into errors on purpose. Only the
# noise `transformers`/`torch` emit on a CPU training or export run is filtered, so a
# new deprecation in this lane's own code still fails the run.
pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
    # torch.onnx traces the graph and warns about constant tensors on every export.
    pytest.mark.filterwarnings("ignore::torch.jit.TracerWarning"),
]


def _texts(n: int = 5) -> list[str]:
    from sieve.model.data import load_corpus
    from sieve.serialise import serialise

    return [
        serialise(r.to_tool_record())
        for r in load_corpus(Path("fixtures/corpus/mini-50.jsonl"))[:n]
    ]


@pytest.fixture(scope="module")
def calibrated_tiny(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """`(torch_dir, onnx_dir)` for a trained tiny model with an operating point written in."""
    return build_calibrated_tiny(tmp_path_factory.mktemp("infer"))


# ------------------------------------------------------------------------ pure helpers


def test_predicted_class_reads_the_argmax_through_labels() -> None:
    probs = np.asarray([[0.2, 0.7, 0.1], [0.05, 0.05, 0.9], [0.8, 0.1, 0.1]])
    assert predicted_class(probs) == ["imperative_benign", "poisoned", "benign"]


def test_class_probabilities_keys_are_exactly_the_three_labels() -> None:
    probs = np.asarray([[0.2, 0.7, 0.1]])
    rows = class_probabilities(probs)
    assert len(rows) == 1
    assert set(rows[0]) == set(LABELS)
    assert rows[0]["imperative_benign"] == pytest.approx(0.7)


def test_class_probabilities_rows_still_sum_to_one() -> None:
    probs = np.asarray([[0.2, 0.7, 0.1], [0.34, 0.33, 0.33]])
    for row in class_probabilities(probs):
        assert sum(row.values()) == pytest.approx(1.0)


def test_helpers_reject_a_block_that_is_not_three_wide() -> None:
    with pytest.raises(ValueError, match="three"):
        predicted_class(np.asarray([[0.5, 0.5]]))
    with pytest.raises(ValueError, match="three"):
        class_probabilities(np.asarray([[0.5, 0.5]]))


# ------------------------------------------------------------------------------ fixture


def test_load_classifier_fixture_returns_the_contract_fake() -> None:
    model = load_classifier("fixture")
    assert isinstance(model, FakeClassifier)
    assert type(model).__module__ == "sieve.protocols"
    assert isinstance(model, Classifier)


# --------------------------------------------------------------------- real artefacts


@pytest.mark.slow
def test_onnx_classifier_returns_three_calibrated_columns(
    calibrated_tiny: tuple[Path, Path],
) -> None:
    _, onnx_dir = calibrated_tiny
    model = OnnxClassifier(onnx_dir)
    probs = model.predict_proba(_texts())
    assert probs.shape == (5, 3)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)
    assert model.thresholds.t_low == CALIBRATION["t_low"]
    assert model.thresholds.temperature == CALIBRATION["temperature"]
    assert isinstance(model, Classifier)


@pytest.mark.slow
def test_torch_classifier_agrees_with_the_onnx_one(
    calibrated_tiny: tuple[Path, Path],
) -> None:
    torch_dir, onnx_dir = calibrated_tiny
    texts = _texts(8)
    assert (
        np.max(
            np.abs(
                TorchClassifier(torch_dir).predict_proba(texts)
                - OnnxClassifier(onnx_dir).predict_proba(texts)
            )
        )
        <= 0.01
    )


@pytest.mark.slow
def test_temperature_is_applied_inside_predict_proba(
    calibrated_tiny: tuple[Path, Path],
) -> None:
    """Callers never scale again: raising the artefact's temperature must flatten the output."""
    _, onnx_dir = calibrated_tiny
    path = onnx_dir / "sieve_config.json"
    original = path.read_text(encoding="utf-8")
    sharp = OnnxClassifier(onnx_dir).predict_proba(_texts(3))
    try:
        config = json.loads(original)
        config["temperature"] = 8.0
        path.write_text(json.dumps(config), encoding="utf-8", newline="\n")
        flat = OnnxClassifier(onnx_dir).predict_proba(_texts(3))
    finally:
        path.write_text(original, encoding="utf-8", newline="\n")
    assert flat.max(axis=1).mean() < sharp.max(axis=1).mean()


@pytest.mark.slow
def test_load_classifier_resolves_a_local_directory(
    calibrated_tiny: tuple[Path, Path],
) -> None:
    torch_dir, onnx_dir = calibrated_tiny
    assert isinstance(load_classifier(str(onnx_dir)), OnnxClassifier)
    assert isinstance(load_classifier(str(torch_dir), use_onnx=False), TorchClassifier)
    # A directory with no ONNX graph falls back to Torch rather than failing.
    assert isinstance(load_classifier(str(torch_dir)), TorchClassifier)


@pytest.mark.slow
def test_model_version_comes_from_the_artefact(calibrated_tiny: tuple[Path, Path]) -> None:
    _, onnx_dir = calibrated_tiny
    assert OnnxClassifier(onnx_dir).model_version.startswith("tiny@")


# ------------------------------------------------------------- there is no default threshold


def test_load_classifier_raises_on_a_null_threshold(tmp_path: Path) -> None:
    config = dict(CALIBRATION, t_low=None, model_version="x@0")
    (tmp_path / "sieve_config.json").write_text(json.dumps(config), encoding="utf-8", newline="\n")
    (tmp_path / "model.onnx").write_bytes(b"not a real graph")
    with pytest.raises(ThresholdsMissing, match="t_low"):
        load_classifier(str(tmp_path))


def test_load_classifier_raises_when_the_artefact_was_never_calibrated(tmp_path: Path) -> None:
    (tmp_path / "model.onnx").write_bytes(b"not a real graph")
    with pytest.raises(ThresholdsMissing):
        load_classifier(str(tmp_path))
