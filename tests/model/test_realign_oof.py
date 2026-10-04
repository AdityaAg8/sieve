"""Repairing v1's OOF arrays: ids back onto the rows they describe (ADR-009)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sieve.model.realign_oof import RealignError, log_softmax, match_rows, realign_fold


def _rows(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, 3)).astype(np.float32), rng.integers(0, 3, size=n)


def test_match_rows_recovers_a_permutation_through_small_noise() -> None:
    fresh, labels = _rows(40)
    perm = np.random.default_rng(1).permutation(40)
    stored = fresh[perm] + np.float32(1e-4)
    got = match_rows(log_softmax(stored), labels[perm], log_softmax(fresh), labels,
                     max_distance=0.05)  # fmt: skip
    assert got.tolist() == perm.tolist()


def test_match_rows_refuses_a_label_disagreement_or_a_distant_match() -> None:
    fresh, labels = _rows(10)
    with pytest.raises(RealignError):
        match_rows(log_softmax(fresh), (labels + 1) % 3, log_softmax(fresh), labels,
                   max_distance=0.05)  # fmt: skip
    with pytest.raises(RealignError):
        match_rows(log_softmax(fresh + np.float32(3.0) * np.eye(3, dtype=np.float32)[labels]),
                   labels, log_softmax(fresh), labels, max_distance=0.05)  # fmt: skip


def test_realign_fold_rewrites_only_the_ids_in_stored_row_order(tmp_path: Path) -> None:
    fresh, labels = _rows(12, seed=3)
    ids = [f"r{i:02d}" for i in range(12)]  # dataset order, as v1 wrote them
    perm = np.random.default_rng(4).permutation(12)
    fold = tmp_path / "fold0"
    fold.mkdir()
    np.save(fold / "oof_logits.npy", fresh[perm])
    np.save(fold / "oof_labels.npy", labels[perm].astype(np.int64))
    (fold / "oof_ids.json").write_text(json.dumps(ids), encoding="utf-8")
    logits_before = (fold / "oof_logits.npy").read_bytes()

    report = realign_fold(fold, ids, labels, lambda: fresh, max_distance=0.05)

    assert json.loads((fold / "oof_ids.json").read_text(encoding="utf-8")) == [ids[i] for i in perm]
    assert (fold / "oof_logits.npy").read_bytes() == logits_before
    assert report["rows"] == 12 and report["moved"] == int((perm != np.arange(12)).sum())
    # Idempotent: a second pass over repaired ids finds nothing to move.
    again = realign_fold(fold, [ids[i] for i in perm], labels[perm], lambda: fresh[perm],
                         max_distance=0.05)  # fmt: skip
    assert again["moved"] == 0


def test_a_near_certain_row_whose_tail_moved_is_still_its_own_match() -> None:
    """bf16 moves a confident row's tail log-probability a lot and its probabilities hardly."""
    fresh = np.array([[20.0, 0.0, -10.0], [0.0, 1.0, 0.0], [0.5, 0.0, 2.0]], dtype=np.float32)
    stored = fresh.copy()
    stored[0, 2] = -15.0
    labels = np.array([0, 1, 2])
    perm = match_rows(log_softmax(stored), labels, log_softmax(fresh), labels, max_distance=0.05)
    assert perm.tolist() == [0, 1, 2]
