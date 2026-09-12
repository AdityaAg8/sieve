import numpy as np
import numpy.typing as npt
import pytest

from sieve.eval.metrics import (
    average_precision,
    binary_counts,
    bootstrap_ci,
    cluster_bootstrap_ci,
    cohens_kappa,
    confusion_matrix,
    expected_calibration_error,
    gwet_ac1,
    macro_f1,
    mcnemar_exact,
    paired_bootstrap_diff,
    precision_recall_fpr,
    t_high_for_precision,
    t_low_for_recall,
    t_low_for_recall_lcb,
)

Y = ["poisoned", "poisoned", "benign", "imperative_benign", "benign", "imperative_benign"]
S = [0.9, 0.6, 0.7, 0.2, 0.1, 0.55]
# Servers must have heterogeneous FPRs, or the cluster statistic has zero variance across
# resamples and "cluster CI is wider than row CI" can never be demonstrated.
G = ["s1", "s1", "s2", "s3", "s3", "s2"]


def test_confusion_matrix_shape_and_values() -> None:
    cm = confusion_matrix(["benign", "poisoned", "poisoned"], ["benign", "benign", "poisoned"])
    assert cm.tolist() == [[1, 0, 0], [0, 0, 0], [1, 0, 1]]


def test_binary_counts() -> None:
    assert binary_counts(Y, S, 0.5) == (2, 2, 0, 2)


def test_precision_recall_fpr() -> None:
    p, r, fpr = precision_recall_fpr(Y, S, 0.5)
    assert (p, r, fpr) == (0.5, 1.0, 0.5)


def test_t_low_is_min_positive_score_at_full_recall() -> None:
    assert t_low_for_recall(Y, S, 1.0) == pytest.approx(0.6)


def test_t_low_partial_recall() -> None:
    assert t_low_for_recall(Y, S, 0.5) == pytest.approx(0.9)


def test_t_high_for_precision() -> None:
    # at t=0.9 precision=1.0; at t=0.7 precision=0.5. Lowest t with precision>=0.95 is 0.9
    assert t_high_for_precision(Y, S, 0.95) == pytest.approx(0.9)


def test_t_high_unattainable_returns_above_one() -> None:
    assert t_high_for_precision(["benign", "poisoned"], [0.9, 0.1], 0.95) > 1.0


def test_macro_f1_perfect() -> None:
    assert macro_f1(Y, Y) == 1.0


def test_average_precision_perfect_ranking() -> None:
    assert average_precision(["poisoned", "poisoned", "benign"], [0.9, 0.8, 0.1]) == 1.0


def test_ece_perfectly_calibrated_is_zero() -> None:
    probs = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
    assert expected_calibration_error(probs, np.array([2, 0])) == 0.0


def test_kappa_perfect_and_chance() -> None:
    assert cohens_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    assert cohens_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0


def test_t_low_raises_without_positives() -> None:
    with pytest.raises(ValueError, match="no positive examples"):
        t_low_for_recall(["benign", "benign"], [0.1, 0.2], 1.0)


def test_average_precision_no_positives_is_zero() -> None:
    assert average_precision(["benign", "benign"], [0.9, 0.1]) == 0.0


def _fpr(yt: npt.NDArray[np.str_], ys: npt.NDArray[np.float64]) -> float:
    return precision_recall_fpr(list(yt), list(ys), 0.5)[2]


def test_bootstrap_ci_brackets_point() -> None:
    point, lo, hi = bootstrap_ci(_fpr, Y, S, n_boot=200, seed=1)
    assert lo <= point <= hi


def test_cluster_bootstrap_ci_brackets_point_and_is_wider_than_row_bootstrap() -> None:
    point, lo, hi = cluster_bootstrap_ci(_fpr, Y, S, G, n_boot=400, seed=1)
    _, rlo, rhi = bootstrap_ci(_fpr, Y, S, n_boot=400, seed=1)
    assert lo <= point <= hi
    # Resampling three servers is coarser than resampling six rows, so the interval cannot be tighter.
    assert (hi - lo) >= (rhi - rlo)


def test_t_low_for_recall_lcb_is_not_above_the_point_threshold() -> None:
    # A lower bound on recall is never more optimistic than the point estimate, so the
    # threshold it admits is never higher than the one full recall admits.
    lcb = t_low_for_recall_lcb(Y, S, G, target=1.0, n_boot=200, seed=0)
    assert lcb <= t_low_for_recall(Y, S, 1.0) + 1e-12


def test_t_low_for_recall_lcb_returns_a_candidate_positive_score() -> None:
    lcb = t_low_for_recall_lcb(Y, S, G, target=0.5, n_boot=200, seed=0)
    assert lcb in {0.9, 0.6}


def test_paired_bootstrap_diff_is_zero_for_identical_scores() -> None:
    point, lo, hi = paired_bootstrap_diff(Y, S, 0.5, S, 0.5, G, n_boot=200, seed=0)
    assert point == 0.0 and lo <= 0.0 <= hi


def test_paired_bootstrap_diff_sign_follows_the_worse_scorer() -> None:
    worse = [0.9, 0.6, 0.9, 0.9, 0.9, 0.9]  # flags every non-poisoned row
    point, _, _ = paired_bootstrap_diff(Y, worse, 0.5, S, 0.5, G, n_boot=200, seed=0)
    assert point == pytest.approx(0.5)  # FPR 1.0 vs 0.5


def test_mcnemar_exact_hand_checked() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 0) == pytest.approx(2 * 0.5**5)  # 0.0625
    assert mcnemar_exact(0, 5) == pytest.approx(0.0625)  # symmetric
    assert mcnemar_exact(1, 1) == 1.0
    assert mcnemar_exact(6, 1) == pytest.approx(0.125)  # 2 * (C(7,0)+C(7,1)) / 2**7


def test_gwet_ac1_hand_checked() -> None:
    assert gwet_ac1(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # Two raters, two categories, 50% observed agreement, balanced marginals:
    # pi = 0.5 for each class, pe = 2 * 0.5 * 0.5 / (2 - 1) = 0.5, AC1 = (0.5 - 0.5) / 0.5 = 0.0
    assert gwet_ac1(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)
    # AC1's point: near-total prevalence of one class does not collapse the statistic the way κ does.
    a = ["a"] * 19 + ["b"]
    b = ["a"] * 19 + ["a"]
    assert gwet_ac1(a, b) > cohens_kappa(a, b)


# The three tests below close coverage gaps that the SAC blocker (see lessons.md) had masked:
# they exercise branches the tests above never reach, following the same "100% is a gate, not
# an aspiration" rationale as the coverage-only tests earlier in this file.


def test_gwet_ac1_single_label_is_perfect_agreement() -> None:
    assert gwet_ac1(["a", "a"], ["a", "a"]) == 1.0


def test_t_low_for_recall_lcb_raises_without_positives() -> None:
    with pytest.raises(ValueError, match="no positive examples"):
        t_low_for_recall_lcb(["benign", "benign"], [0.1, 0.2], ["s1", "s1"], target=0.5, n_boot=10)


def test_t_low_for_recall_lcb_returns_on_first_candidate_when_target_is_trivial() -> None:
    # target=0.0 always clears on the highest-scoring candidate, exercising the early-return
    # success branch (as opposed to the target=1.0/0.5 tests above, which fall through to the
    # last-candidate fallback given this dataset's cluster structure).
    assert t_low_for_recall_lcb(Y, S, G, target=0.0, n_boot=50, seed=0) == pytest.approx(0.9)
