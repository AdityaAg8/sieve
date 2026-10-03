"""M2, FP reduction against the YARA baseline (training plan D7), on fixtures with known answers."""

from __future__ import annotations

import math

import pytest

from sieve.eval.metrics import clustered_mcnemar_p, fp_reduction


def _fixture() -> tuple[list[str], list[bool], list[bool], list[str]]:
    """20 negatives on 4 servers: YARA flags 10, Sieve 4 (all within YARA's 10). 5 positives,
    both catch all of them."""
    truth = ["benign"] * 20 + ["poisoned"] * 5
    baseline = [i < 10 for i in range(20)] + [True] * 5
    sieve = [i < 4 for i in range(20)] + [True] * 5
    groups = [f"s{i % 4}" for i in range(20)] + [f"p{i}" for i in range(5)]
    return truth, sieve, baseline, groups


def test_the_relative_and_absolute_reductions_are_the_hand_computed_ones() -> None:
    truth, sieve, baseline, groups = _fixture()
    r = fp_reduction(truth, sieve, baseline, groups, n_boot=300, seed=0)
    assert r.baseline_fpr == pytest.approx(0.5)
    assert r.sieve_fpr == pytest.approx(0.2)
    assert r.absolute == pytest.approx(0.3)
    assert r.relative == pytest.approx(0.6)
    assert r.relative_lo <= r.relative <= r.relative_hi
    assert r.absolute_lo <= r.absolute <= r.absolute_hi


def test_mcnemar_counts_negatives_only() -> None:
    """Six negatives YARA flags and Sieve clears, none the other way: p = 2 / 2**6.

    The positives Sieve misses and YARA catches would change an all-rows McNemar; they must not
    change this one.
    """
    truth, sieve, baseline, groups = _fixture()
    exact = fp_reduction(truth, sieve, baseline, groups, n_boot=50).mcnemar_p
    assert exact == pytest.approx(2 / 2**6)
    sieve_missing = sieve[:20] + [False] * 5
    assert fp_reduction(truth, sieve_missing, baseline, groups, n_boot=50).mcnemar_p == exact


def test_the_cluster_adjusted_mcnemar_reduces_to_the_plain_one_for_singleton_clusters() -> None:
    """Durkalski et al. (2003): X2 = (sum_k d_k)^2 / sum_k d_k^2, d_k = b_k - c_k per cluster.

    With one row per cluster it is the uncorrected McNemar chi-square (b - c)^2 / (b + c).
    """
    b_rows = [(True, False)] * 6  # (baseline flagged, sieve flagged)
    c_rows = [(False, True)] * 2
    rows = b_rows + c_rows
    groups = [f"g{i}" for i in range(len(rows))]
    statistic = (6 - 2) ** 2 / (6 + 2)
    assert clustered_mcnemar_p([b for b, _ in rows], [s for _, s in rows], groups) == pytest.approx(
        math.erfc(math.sqrt(statistic / 2))
    )


def test_one_cluster_holding_every_discordance_is_one_observation() -> None:
    rows = [(True, False)] * 6
    p = clustered_mcnemar_p([b for b, _ in rows], [s for _, s in rows], ["one"] * 6)
    assert p == pytest.approx(math.erfc(math.sqrt(0.5)))  # X2 = 36 / 36 = 1


def test_no_discordance_has_p_one() -> None:
    assert clustered_mcnemar_p([True, False], [True, False], ["a", "b"]) == 1.0


def test_the_fixture_reports_the_clustered_p_beside_the_exact_one() -> None:
    truth, sieve, baseline, groups = _fixture()
    r = fp_reduction(truth, sieve, baseline, groups, n_boot=50)
    # Discordant negatives are rows 4..9: servers s0, s1, s2, s3, s0, s1 -> d = (2, 2, 1, 1).
    statistic = (2 + 2 + 1 + 1) ** 2 / (4 + 4 + 1 + 1)
    assert r.mcnemar_p_clustered == pytest.approx(math.erfc(math.sqrt(statistic / 2)))


def test_the_reduction_travels_with_the_pipeline_recall_comparison() -> None:
    truth, sieve, baseline, groups = _fixture()
    same = fp_reduction(truth, sieve, baseline, groups, n_boot=50)
    assert (same.sieve_recall, same.baseline_recall) == (1.0, 1.0)
    assert same.recall_not_lower is True

    lower = fp_reduction(truth, sieve[:20] + [False] + [True] * 4, baseline, groups, n_boot=50)
    assert lower.sieve_recall == pytest.approx(0.8)
    assert lower.recall_not_lower is False


def test_no_baseline_false_positive_leaves_the_relative_reduction_undefined() -> None:
    truth = ["benign"] * 6 + ["poisoned"] * 2
    baseline = [False] * 6 + [True] * 2
    sieve = [True] + [False] * 5 + [True] * 2
    groups = [f"s{i % 3}" for i in range(8)]
    r = fp_reduction(truth, sieve, baseline, groups, n_boot=40)
    assert math.isnan(r.relative)
    assert r.n_undefined == 40
    assert r.absolute == pytest.approx(-1 / 6)


def test_one_cluster_gives_a_degenerate_interval() -> None:
    truth, sieve, baseline, _ = _fixture()
    r = fp_reduction(truth, sieve, baseline, ["one"] * len(truth), n_boot=30)
    assert r.relative_lo == r.relative_hi == r.relative
