"""Cross-fitted reporting (training plan D6).

In-sample, recall at `T_low` is tautological (`T_low` was chosen to clear it on the same rows)
and ECE after temperature is fitted and scored on the same rows. Cross-fitting by fold fits the
temperature and `T_low` on the other folds and scores each fold with them. The shipped
thresholds still come from the pooled OOF fit.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from sieve.eval.metrics import (
    calibration_error,
    expected_calibration_error,
    reliability_bins,
)
from sieve.model import thresholds as th
from sieve.model.calibrate import apply, crossfit_report, fit_temperature

POISONED = 2


def test_reliability_bins_partition_the_rows_and_carry_their_means() -> None:
    conf = np.array([0.05, 0.1, 0.55, 0.6, 0.95, 1.0])
    outcome = np.array([0.0, 1.0, 1.0, 0.0, 1.0, 1.0])
    bins = reliability_bins(conf, outcome, n_bins=2)
    assert [b["n"] for b in bins] == [2, 4]
    assert bins[0]["lo"] == 0.0 and bins[1]["hi"] == 1.0
    assert bins[0]["mean_confidence"] == pytest.approx(0.075)
    assert bins[0]["observed"] == pytest.approx(0.5)
    assert bins[1]["mean_confidence"] == pytest.approx(0.775)
    assert bins[1]["observed"] == pytest.approx(0.75)
    # ECE is the row-weighted gap: 2/6 * 0.425 + 4/6 * 0.025.
    assert calibration_error(bins) == pytest.approx(2 / 6 * 0.425 + 4 / 6 * 0.025)


def test_empty_bins_are_kept_with_no_means() -> None:
    bins = reliability_bins(np.array([0.9, 0.95]), np.array([1.0, 1.0]), n_bins=4)
    assert [b["n"] for b in bins] == [0, 0, 0, 2]
    assert bins[0]["mean_confidence"] is None and bins[0]["observed"] is None


def test_the_top_label_ece_is_unchanged_by_the_refactor() -> None:
    rng = np.random.default_rng(3)
    probs = rng.dirichlet([1, 1, 1], size=500)
    labels = rng.integers(0, 3, size=500)
    conf, pred = probs.max(axis=1), probs.argmax(axis=1)
    by_hand = calibration_error(reliability_bins(conf, (pred == labels).astype(float)))
    assert expected_calibration_error(probs, labels) == pytest.approx(by_hand, abs=1e-12)


def test_the_poisoned_column_ece_of_a_calibrated_column_is_small() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(size=20_000)
    y = (rng.uniform(size=20_000) < p).astype(float)
    assert calibration_error(reliability_bins(p, y)) < 0.02


def _folds(
    sizes: list[int], *, seed: int = 0, scale: float = 3.0
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int_], list[str], npt.NDArray[np.int_]]:
    """Overconfident logits over `len(sizes)` folds, servers nested in folds."""
    rng = np.random.default_rng(seed)
    n = sum(sizes)
    base = rng.normal(0.0, 1.2, size=(n, 3))
    probs = np.exp(base) / np.exp(base).sum(axis=1, keepdims=True)
    labels = np.asarray([rng.choice(3, p=row) for row in probs], dtype=np.int64)
    fold_of = np.repeat(np.arange(len(sizes)), sizes)
    groups = [f"f{f}-s{i % 12}" for i, f in enumerate(fold_of)]
    return base * scale, labels, groups, fold_of


def test_crossfit_ece_and_recall_match_a_hand_rolled_loop() -> None:
    logits, labels, groups, fold_of = _folds([150, 160, 140, 155, 145])
    report = crossfit_report(logits, labels, groups, fold_of, target=0.9, n_boot=60)

    crossfit_probs = np.empty_like(logits)
    hits = positives = 0
    temps, lows = [], []
    for k in range(5):
        train, test = fold_of != k, fold_of == k
        t = fit_temperature(logits[train], labels[train])
        temps.append(t)
        crossfit_probs[test] = apply(logits[test], t)
        train_probs = apply(logits[train], t)
        train_groups = [g for g, m in zip(groups, train, strict=True) if m]
        low = th.select(
            train_probs, labels[train], train_groups, temperature=t, recall_target=0.9, n_boot=60
        ).t_low
        lows.append(low)
        scores = np.asarray(th.poisoned_scores(crossfit_probs[test]))
        pos = labels[test] == POISONED
        hits += int((scores[pos] >= low).sum())
        positives += int(pos.sum())

    assert report["crossfit_temperature_per_fold"] == pytest.approx(temps)
    assert report["crossfit_t_low_per_fold"] == pytest.approx(lows)
    assert report["recall_at_t_low_crossfit"] == pytest.approx(hits / positives)
    assert report["ece_after_crossfit"] == pytest.approx(
        expected_calibration_error(crossfit_probs, labels)
    )
    column = crossfit_probs[:, POISONED]
    assert report["ece_poisoned_after_crossfit"] == pytest.approx(
        calibration_error(reliability_bins(column, (labels == POISONED).astype(float)))
    )
    assert report["ece_kind"].startswith("top-label")
    reliability = report["reliability"]
    for key in ("top_label_after_crossfit", "poisoned_after_crossfit"):
        assert sum(b["n"] for b in reliability[key]) == len(labels)


def test_a_fold_unlike_the_others_is_missed_out_of_fold_but_not_in_sample() -> None:
    """The tautology D6 removes: fold 4's positives score far below everyone else's.

    In-sample, `T_low` is pulled down to catch them. Out of fold, `T_low` is set on folds 0-3,
    whose positives all score high, so fold 4's positives fall below it.
    """
    rng = np.random.default_rng(1)
    rows_per_fold, pos_per_fold = 60, 12
    logits_blocks, label_blocks = [], []
    for fold in range(5):
        block = rng.normal(0.0, 0.3, size=(rows_per_fold, 3))
        labels = np.zeros(rows_per_fold, dtype=np.int64)
        labels[:pos_per_fold] = POISONED
        labels[pos_per_fold:] = rng.integers(0, 2, size=rows_per_fold - pos_per_fold)
        block[:pos_per_fold, POISONED] += -2.0 if fold == 4 else 6.0
        block[pos_per_fold:, POISONED] -= 6.0
        logits_blocks.append(block)
        label_blocks.append(labels)
    logits = np.concatenate(logits_blocks)
    labels = np.concatenate(label_blocks)
    fold_of = np.repeat(np.arange(5), rows_per_fold)
    groups = [f"f{f}-s{i % 6}" for i, f in enumerate(fold_of)]

    t = fit_temperature(logits, labels)
    probs = apply(logits, t)
    in_sample = th.select(probs, labels, groups, temperature=t, recall_target=0.95, n_boot=100)
    report = crossfit_report(logits, labels, groups, fold_of, target=0.95, n_boot=100)

    assert in_sample.val_recall_at_t_low == pytest.approx(1.0)
    assert report["recall_at_t_low_crossfit"] == pytest.approx(0.8)  # fold 4's 12 of 60 missed


def test_one_fold_cannot_be_cross_fitted() -> None:
    logits, labels, groups, fold_of = _folds([200])
    report = crossfit_report(logits, labels, groups, fold_of, target=0.9, n_boot=20)
    assert report["recall_at_t_low_crossfit"] is None
    assert report["ece_after_crossfit"] is None
    assert report["reliability"]["top_label_after_crossfit"] is None
