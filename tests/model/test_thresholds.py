from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from sieve.eval.metrics import precision_recall_fpr, t_low_for_recall
from sieve.model import thresholds as th
from sieve.protocols import Thresholds
from sieve.types import LABELS

N_BOOT = 120


def _pooled(
    n_servers: int = 24, per_server: int = 5, seed: int = 0
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int_], list[str]]:
    """Separable-but-overlapping three-class probabilities with a real server structure."""
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    for server in range(n_servers):
        for _ in range(per_server):
            label = int(rng.choice(3, p=[0.45, 0.3, 0.25]))
            base = np.full(3, 0.35)
            base[label] += rng.uniform(0.05, 0.75)
            base = base + rng.uniform(0.0, 0.9, size=3)
            rows.append(list(base / base.sum()))
            labels.append(label)
            groups.append(f"server-{server}")
    return np.asarray(rows), np.asarray(labels, dtype=np.int64), groups


def _poisoned(probs: npt.NDArray[np.float64]) -> list[float]:
    return [float(p) for p in probs[:, 2]]


def _truth(labels: npt.NDArray[np.int_]) -> list[str]:
    return [LABELS[int(i)] for i in labels]


def test_select_returns_t_low_not_above_t_high() -> None:
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.3, n_boot=N_BOOT)
    assert isinstance(chosen, Thresholds)
    assert chosen.t_low <= chosen.t_high
    assert chosen.temperature == 1.3


def test_the_fixture_leaves_a_real_abstain_band() -> None:
    """Guards the routing-rate test below from passing on a degenerate empty band."""
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)
    assert chosen.t_low < chosen.t_high
    assert chosen.routing_rate > 0.0


def test_an_empty_abstain_band_collapses_t_high_onto_t_low() -> None:
    """A classifier good enough to clear the precision target below t_low routes nothing."""
    labels = np.asarray([0, 0, 2, 2] * 6, dtype=np.int64)
    probs = np.zeros((len(labels), 3))
    for i, label in enumerate(labels):
        probs[i, int(label)] = 0.99
        probs[i, 1] = 0.01
    probs = probs / probs.sum(axis=1, keepdims=True)
    groups = [f"s{i // 4}" for i in range(len(labels))]
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)
    assert chosen.t_low == chosen.t_high
    assert chosen.routing_rate == 0.0


def test_t_low_is_never_more_optimistic_than_the_point_estimate() -> None:
    """A lower bound cannot sit above the point-estimate threshold on the same data."""
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)
    naive = t_low_for_recall(_truth(labels), _poisoned(probs), target=0.98)
    assert chosen.t_low <= naive


def test_routing_rate_is_the_share_of_rows_in_the_abstain_band() -> None:
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)
    scores = np.asarray(_poisoned(probs))
    expected = float(np.mean((scores >= chosen.t_low) & (scores < chosen.t_high)))
    assert chosen.routing_rate == pytest.approx(expected)


def test_reported_recall_and_precision_match_the_chosen_thresholds() -> None:
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)
    truth, scores = _truth(labels), _poisoned(probs)
    _, recall, _ = precision_recall_fpr(truth, scores, chosen.t_low)
    precision, _, _ = precision_recall_fpr(truth, scores, chosen.t_high)
    assert chosen.val_recall_at_t_low == pytest.approx(recall)
    assert chosen.val_precision_at_t_high == pytest.approx(precision)


def test_t_low_spread_returns_five_values_that_bracket_t_low() -> None:
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT, seed=0)
    spread = th.t_low_spread(probs, labels, groups, refits=5, n_boot=N_BOOT)
    assert len(spread) == 5
    assert min(spread) <= chosen.t_low <= max(spread)


def test_select_raises_without_a_poisoned_row() -> None:
    probs, labels, groups = _pooled()
    labels = np.where(labels == 2, 0, labels)
    with pytest.raises(ValueError, match="positive"):
        th.select(probs, labels, groups, temperature=1.0, n_boot=N_BOOT)


# ----------------------------------------------------------------------------- curves


def test_curves_are_monotone_and_their_bounds_contain_the_point(tmp_path: Path) -> None:
    probs, labels, groups = _pooled()
    path = tmp_path / "curves.json"
    th.write_curves(probs, labels, groups, path, n_thresholds=40, n_boot=N_BOOT)
    curves = json.loads(path.read_text(encoding="utf-8"))

    recall_curve = curves["recall_vs_threshold"]
    assert len(recall_curve) == 40
    thresholds = [row[0] for row in recall_curve]
    recalls = [row[1] for row in recall_curve]
    assert thresholds == sorted(thresholds)
    assert all(a >= b - 1e-12 for a, b in pairwise(recalls))
    for _t, point, lo, hi in recall_curve:
        assert lo <= point <= hi

    fpr_curve = curves["fpr_vs_recall"]
    recall_axis = [row[0] for row in fpr_curve]
    fprs = [row[1] for row in fpr_curve]
    assert recall_axis == sorted(recall_axis)
    assert all(a <= b + 1e-12 for a, b in pairwise(fprs))
    for _r, point, lo, hi in fpr_curve:
        assert lo <= point <= hi


# ------------------------------------------------------------------------------- load


def _config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "temperature": 1.4,
        "t_low": 0.12,
        "t_high": 0.88,
        "val_recall_at_t_low": 0.99,
        "val_precision_at_t_high": 0.96,
        "routing_rate": 0.18,
    }
    base.update(overrides)
    return base


def test_load_reads_the_artefact(tmp_path: Path) -> None:
    (tmp_path / "sieve_config.json").write_text(
        json.dumps(_config()), encoding="utf-8", newline="\n"
    )
    loaded = th.load(tmp_path)
    assert loaded.t_low == 0.12
    assert loaded.t_high == 0.88
    assert loaded.temperature == 1.4


@pytest.mark.parametrize(
    "field",
    [
        "temperature",
        "t_low",
        "t_high",
        "val_recall_at_t_low",
        "val_precision_at_t_high",
        "routing_rate",
    ],
)
def test_load_raises_on_any_null(tmp_path: Path, field: str) -> None:
    (tmp_path / "sieve_config.json").write_text(
        json.dumps(_config(**{field: None})), encoding="utf-8", newline="\n"
    )
    with pytest.raises(th.ThresholdsMissing, match=field):
        th.load(tmp_path)


def test_load_raises_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(th.ThresholdsMissing, match="sieve_config.json"):
        th.load(tmp_path)


def test_there_is_no_default_threshold() -> None:
    """The sentinel is NaN, so a forgotten calibration can never quietly route anything."""
    assert np.isnan(th.UNCALIBRATED.t_low)
    assert np.isnan(th.UNCALIBRATED.t_high)


def test_write_shared_config_fills_every_slot(tmp_path: Path) -> None:
    probs, labels, groups = _pooled()
    chosen = th.select(probs, labels, groups, temperature=1.2, n_boot=N_BOOT)
    payload = th.threshold_payload(chosen, t_low_spread=[0.1, 0.2], ece_before=0.2, ece_after=0.03)
    assert payload["t_low_spread"] == [0.1, 0.2]
    assert payload["ece_after"] < payload["ece_before"]
    assert set(payload) >= {
        "temperature",
        "t_low",
        "t_high",
        "t_low_spread",
        "ece_before",
        "ece_after",
        "routing_rate",
        "val_recall_at_t_low",
        "val_precision_at_t_high",
    }
