"""AUROC / AUPRC with cluster-bootstrap CIs (training plan D4)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

from sieve.eval.metrics import auprc, auroc, cluster_keys, ranking_ci


def _fixture(seed: int, decimals: int) -> tuple[list[str], list[float]]:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(30, 300))
    y = rng.choice(["benign", "imperative_benign", "poisoned"], size=n, p=[0.5, 0.3, 0.2])
    y[0], y[1] = "poisoned", "benign"
    s = np.where(y == "poisoned", rng.beta(4, 2, n), rng.beta(2, 4, n))
    return list(y), [float(x) for x in np.round(s, decimals)]


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("decimals", [1, 2, 6])
def test_auroc_and_auprc_match_sklearn(seed: int, decimals: int) -> None:
    y, s = _fixture(seed, decimals)
    truth = [t == "poisoned" for t in y]
    assert auroc(y, s) == pytest.approx(roc_auc_score(truth, s), abs=1e-12)
    assert auprc(y, s) == pytest.approx(average_precision_score(truth, s), abs=1e-12)


def test_ties_are_order_independent() -> None:
    y, s = _fixture(3, 1)  # one decimal: heavy ties
    rng = np.random.default_rng(0)
    for _ in range(5):
        perm = rng.permutation(len(y))
        y2, s2 = [y[i] for i in perm], [s[i] for i in perm]
        assert auroc(y2, s2) == pytest.approx(auroc(y, s), abs=1e-12)
        assert auprc(y2, s2) == pytest.approx(auprc(y, s), abs=1e-12)


def test_one_class_is_undefined_not_zero() -> None:
    assert math.isnan(auroc(["benign"] * 3, [0.1, 0.2, 0.3]))
    assert math.isnan(auprc(["benign"] * 3, [0.1, 0.2, 0.3]))


def test_the_ci_brackets_the_point_and_resamples_whole_clusters() -> None:
    y, s = _fixture(1, 3)
    servers = [f"srv{i % 15}" for i in range(len(y))]
    for metric in ("auroc", "auprc"):
        r = ranking_ci(y, s, servers, metric=metric, n_boot=300, seed=0)
        assert r.lo <= r.point <= r.hi
        assert r.point == pytest.approx(auroc(y, s) if metric == "auroc" else auprc(y, s))
    single = ranking_ci(y, s, ["one"] * len(y), metric="auroc", n_boot=50, seed=0)
    assert single.lo == single.hi == single.point  # one cluster: every replicate is the data


def test_cluster_keys_are_server_or_template_by_server() -> None:
    assert cluster_keys(["a", "b"]) == ["a", "b"]
    assert cluster_keys(["a", "a"], templates=["t1", "t2"]) == ["t1×a", "t2×a"]
    with pytest.raises(ValueError, match="length"):
        cluster_keys(["a"], templates=["t1", "t2"])
