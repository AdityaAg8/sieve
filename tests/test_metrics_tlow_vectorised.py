"""`t_low_for_recall_lcb`, vectorised (training plan D1): the same answer, far faster."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from sieve.eval.metrics import (
    cluster_bootstrap_ci,
    precision_recall_fpr,
    t_low_for_recall_lcb,
)


def _reference(
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    target: float,
    n_boot: int,
    seed: int,
) -> float:
    """One bootstrap per candidate, the pre-D1 loop, frozen as the oracle (with D3's rule:
    a replicate holding no positive has no recall and is excluded, not scored 0)."""
    cands = sorted({float(s) for t, s in zip(y_true, y_score, strict=True) if t == "poisoned"},
                   reverse=True)  # fmt: skip

    def recall_at(threshold: float):  # type: ignore[no-untyped-def]
        def stat(yt, ys):  # type: ignore[no-untyped-def]
            if not (np.asarray(yt) == "poisoned").any():
                return float("nan")
            return precision_recall_fpr(list(yt), list(ys), threshold)[1]

        return stat

    for t in cands:
        _, lo, _ = cluster_bootstrap_ci(recall_at(t), y_true, y_score, groups, n_boot=n_boot,
                                        seed=seed)  # fmt: skip
        if lo >= target:
            return t
    return cands[-1]


def _fixture(seed: int) -> tuple[list[str], list[float], list[str]]:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(40, 160))
    n_servers = int(rng.integers(5, 40))
    labels = rng.choice(["benign", "imperative_benign", "poisoned"], size=n, p=[0.5, 0.3, 0.2])
    labels[0] = "poisoned"
    scores = np.where(labels == "poisoned", rng.beta(5, 2, n), rng.beta(2, 5, n))
    scores = np.round(scores, int(rng.integers(1, 4)))  # ties, on purpose
    servers = [f"s{int(i)}" for i in rng.integers(0, n_servers, n)]
    return list(labels), [float(x) for x in scores], servers


@pytest.mark.parametrize("seed", range(20))
def test_the_vectorised_t_low_equals_the_reference(seed: int) -> None:
    y, s, g = _fixture(seed)
    target = [0.5, 0.8, 0.9, 0.95, 0.98][seed % 5]
    want = _reference(y, s, g, target, n_boot=200, seed=seed)
    assert t_low_for_recall_lcb(y, s, g, target=target, n_boot=200, seed=seed) == want


def test_a_full_size_selection_takes_seconds_not_minutes() -> None:
    """1,101 OOF rows, 1,000 replicates: 77 s before D1."""
    import time

    rng = np.random.default_rng(0)
    n = 1101
    labels = rng.choice(["benign", "imperative_benign", "poisoned"], size=n, p=[0.5, 0.27, 0.23])
    scores = np.where(labels == "poisoned", rng.beta(8, 1.5, n), rng.beta(1.5, 8, n))
    servers = [f"s{int(i)}" for i in rng.integers(0, 250, n)]
    started = time.perf_counter()
    t_low_for_recall_lcb(list(labels), [float(x) for x in scores], servers, n_boot=1000)
    assert time.perf_counter() - started < 5.0
