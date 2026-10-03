"""Undefined bootstrap replicates and the `t_low` fallback (training plan D3)."""

from __future__ import annotations

import numpy as np
import pytest

from sieve.eval.metrics import (
    cluster_bootstrap,
    cluster_bootstrap_ci,
    cluster_bootstrap_curve,
    t_low_for_recall_lcb,
    t_low_selection,
)


def _three_positive_servers(negative_servers: int = 20) -> tuple[list[str], list[float], list[str]]:
    y = ["poisoned"] * 3 + ["benign"] * negative_servers
    s = [0.9, 0.8, 0.7] + [0.1] * negative_servers
    g = ["p0", "p1", "p2"] + [f"n{i}" for i in range(negative_servers)]
    return y, s, g


def test_three_positives_on_three_servers_have_a_lower_bound_of_one() -> None:
    """A replicate that drew no positive server has no recall: it is undefined, not zero.

    With 20 negative-only servers, (20/23)^23 = 4% of replicates hold no positive; counting
    them as recall 0 put the 2.5% quantile at 0 and pushed t_low to its fallback.
    """
    y, s, g = _three_positive_servers()
    sel = t_low_selection(y, s, g, target=0.98, n_boot=1000, seed=0)
    assert sel.lower_bound == 1.0
    assert sel.t_low == pytest.approx(0.7)
    assert sel.n_undefined > 25  # ~4% of 1,000
    assert sel.fallback is False
    assert sel.quantile == 0.025
    assert t_low_for_recall_lcb(y, s, g, target=0.98, n_boot=1000) == sel.t_low


def test_an_unreachable_target_says_it_fell_back() -> None:
    rng = np.random.default_rng(1)
    y = ["poisoned"] * 30 + ["benign"] * 30
    s = list(rng.uniform(0, 1, 60))
    g = [f"s{i % 6}" for i in range(60)]
    sel = t_low_selection(y, s, g, target=1.01, n_boot=200, seed=0)
    assert sel.fallback is True
    assert sel.t_low == pytest.approx(min(s[:30]))


def test_the_ci_ignores_undefined_replicates_and_counts_them() -> None:
    y, s, g = _three_positive_servers()

    def recall(yt: np.ndarray, ys: np.ndarray) -> float:
        pos = yt == "poisoned"
        return float(np.mean(ys[pos] >= 0.7)) if pos.any() else float("nan")

    result = cluster_bootstrap(recall, y, s, g, n_boot=500, seed=0)
    assert result.n_undefined > 0
    assert (result.point, result.lo, result.hi) == (1.0, 1.0, 1.0)
    assert cluster_bootstrap_ci(recall, y, s, g, n_boot=500, seed=0) == (1.0, 1.0, 1.0)


def test_the_curve_ignores_replicates_without_positives() -> None:
    y, s, g = _three_positive_servers()
    curves = cluster_bootstrap_curve(y, s, g, np.array([0.7]), n_boot=500, seed=0)
    point, lo, hi = curves["recall"][:, 0]
    assert (point, lo, hi) == (1.0, 1.0, 1.0)
