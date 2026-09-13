"""I1.1 -- the process-wide classifier factory (`sieve.api.deps.get_classifier`).

The factory is the only seam between L5's surfaces and L3's artefact loader, so the things
asserted here are the ways it can be wrong: it can serve a fake when a real artefact was
configured, it can reload the artefact on every request, and it can fall back to a fake when
the artefact is missing instead of refusing to serve.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sieve.api import deps
from sieve.config import settings
from sieve.model.thresholds import ThresholdsMissing
from sieve.protocols import Classifier, FakeClassifier
from tests.model.artefacts import CALIBRATION, build_calibrated_tiny

# Building the artefact trains and exports a real (tiny) model; the same targeted filters
# `tests/model/test_infer.py` uses apply here, and nothing else is silenced.
ARTEFACT_MARKS = [
    pytest.mark.slow,
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
    pytest.mark.filterwarnings("ignore::torch.jit.TracerWarning"),
]


def test_the_fixture_id_serves_the_contract_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_id", "fixture")
    model = deps.get_classifier()
    assert isinstance(model, FakeClassifier)
    assert type(model).__module__ == "sieve.protocols"
    assert isinstance(model, Classifier)


def test_the_classifier_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading ONNX costs seconds; a per-request load would pay that on every request."""
    monkeypatch.setattr(settings, "model_id", "fixture")
    assert deps.get_classifier() is deps.get_classifier()


def test_changing_the_configured_model_rebuilds_the_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache is keyed on the three settings that choose the artefact, not on nothing."""
    monkeypatch.setattr(settings, "model_id", "fixture")
    first = deps.get_classifier()
    monkeypatch.setattr(settings, "model_revision", "some-revision")
    assert deps.get_classifier() is not first


def test_a_missing_artefact_raises_rather_than_serving_a_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uncalibrated directory is a configuration error, not a reason to fake an answer."""
    (tmp_path / "model.onnx").write_bytes(b"not a real graph")
    monkeypatch.setattr(settings, "model_id", str(tmp_path))
    with pytest.raises(ThresholdsMissing):
        deps.get_classifier()


def test_the_active_classifier_is_logged_at_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "model_id", "fixture")
    monkeypatch.setattr(settings, "model_revision", "log-check")
    with caplog.at_level(logging.INFO, logger="sieve.api.deps"):
        deps.get_classifier()
    assert any("fixture" in r.getMessage() for r in caplog.records), caplog.text


class TestRealArtefact:
    """The artefact path, against a directory L3's real writers produced."""

    pytestmark = ARTEFACT_MARKS

    @pytest.fixture(scope="class")
    def artefacts(self, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
        return build_calibrated_tiny(tmp_path_factory.mktemp("deps-artefact"))

    def test_a_local_artefact_directory_loads_with_its_own_thresholds(
        self, artefacts: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sieve.model.infer import OnnxClassifier

        _, onnx_dir = artefacts
        monkeypatch.setattr(settings, "model_id", str(onnx_dir))
        model = deps.get_classifier()
        assert isinstance(model, OnnxClassifier)
        assert model.thresholds.t_low == CALIBRATION["t_low"]
        assert model.thresholds.t_high == CALIBRATION["t_high"]
        assert model.thresholds.temperature == CALIBRATION["temperature"]

    def test_the_torch_artefact_loads_when_onnx_is_off(
        self, artefacts: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sieve.model.infer import TorchClassifier

        torch_dir, _ = artefacts
        monkeypatch.setattr(settings, "model_id", str(torch_dir))
        monkeypatch.setattr(settings, "use_onnx", False)
        assert isinstance(deps.get_classifier(), TorchClassifier)
