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
    got, _ = match_rows(log_softmax(stored), labels[perm], log_softmax(fresh), labels,
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
    perm, _ = match_rows(log_softmax(stored), labels, log_softmax(fresh), labels, max_distance=0.05)
    assert perm.tolist() == [0, 1, 2]


def test_the_tolerance_follows_the_precision_the_logits_were_produced_in() -> None:
    """bf16 autocast (ModernBERT) drifts up to ~0.10 in probability against an fp32 re-score
    (measured on modernbert-base-s2 fold0); fp32 artefacts stay held to 0.05."""
    from sieve.model.realign_oof import tolerance_for

    assert tolerance_for("fp32") == 0.05
    assert tolerance_for("bf16") == 0.25


def test_a_refused_fold_leaves_every_fold_untouched(tmp_path: Path) -> None:
    """Two-phase: every fold is matched before any `oof_ids.json` is rewritten (review P2)."""
    from sieve.model.realign_oof import realign_folds

    fresh, labels = _rows(10, seed=5)
    ids = [f"r{i}" for i in range(10)]
    perm = np.random.default_rng(6).permutation(10)
    dirs = []
    for k in range(2):
        fold = tmp_path / f"fold{k}"
        fold.mkdir()
        np.save(fold / "oof_logits.npy", fresh[perm])
        np.save(fold / "oof_labels.npy", labels[perm].astype(np.int64))
        (fold / "oof_ids.json").write_text(json.dumps(ids), encoding="utf-8")
        dirs.append(fold)
    far = fresh + np.float32(5.0) * np.eye(3, dtype=np.float32)[labels]
    jobs = [(dirs[0], ids, labels, lambda: fresh), (dirs[1], ids, labels, lambda: far)]
    with pytest.raises(RealignError):
        realign_folds(jobs, max_distance=0.05)
    for fold in dirs:
        assert json.loads((fold / "oof_ids.json").read_text(encoding="utf-8")) == ids


def test_an_ambiguous_match_between_distinguishable_rows_is_refused() -> None:
    """Identity, not drift: a stored row equally close to two different fresh rows of its label
    cannot be placed, however small its distance (review: drift alone is not evidence)."""
    from sieve.model.realign_oof import RealignError

    fresh = np.log(np.array([[0.70, 0.20, 0.10], [0.60, 0.30, 0.10]], dtype=np.float64))
    stored = np.log(np.array([[0.65, 0.25, 0.10], [0.65, 0.25, 0.10]], dtype=np.float64))
    labels = np.array([0, 0])
    with pytest.raises(RealignError, match="ambiguous"):
        match_rows(stored, labels, fresh, labels, max_distance=0.25)


def test_a_tie_between_practically_identical_rows_is_accepted() -> None:
    """Two confident rows of one label with the same probabilities: either assignment changes
    no metric, so the tie is not a refusal."""
    fresh = np.log(np.array([[0.99, 0.005, 0.005], [0.99, 0.006, 0.004]], dtype=np.float64))
    labels = np.array([0, 0])
    perm, _ = match_rows(fresh[::-1].copy(), labels, fresh, labels, max_distance=0.05)
    assert sorted(perm.tolist()) == [0, 1]


def test_bf16_artefacts_get_the_wider_cap() -> None:
    from sieve.model.realign_oof import tolerance_for

    assert tolerance_for("bf16") == 0.25


def test_a_bf16_budget_admits_and_names_a_few_ambiguous_rows(tmp_path: Path) -> None:
    """bf16 drift can leave a row genuinely ambiguous; within the budget it keeps the optimal
    assignment as its best guess and is named in the report, never silently placed."""
    fresh = np.log(np.array([[0.70, 0.20, 0.10], [0.60, 0.30, 0.10]], dtype=np.float64))
    stored = np.log(np.array([[0.65, 0.25, 0.10], [0.65, 0.25, 0.10]], dtype=np.float64))
    fold = tmp_path / "fold0"
    fold.mkdir()
    np.save(fold / "oof_logits.npy", stored.astype(np.float32))
    np.save(fold / "oof_labels.npy", np.array([0, 0], dtype=np.int64))
    (fold / "oof_ids.json").write_text(json.dumps(["a", "b"]), encoding="utf-8")
    report = realign_fold(fold, ["a", "b"], np.array([0, 0]), lambda: fresh, max_distance=0.25,
                          max_ambiguous=2)  # fmt: skip
    assert sorted(report["ambiguous"]) == ["a", "b"]
    with pytest.raises(RealignError, match="ambiguous"):
        realign_fold(fold, ["a", "b"], np.array([0, 0]), lambda: fresh, max_distance=0.25,
                     max_ambiguous=1)  # fmt: skip
