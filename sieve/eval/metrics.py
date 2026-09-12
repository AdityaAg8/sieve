"""Pure metric functions over NumPy arrays. 100% covered. Shared by baseline and model lanes."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
import numpy.typing as npt

Arr = npt.NDArray[np.float64]
StrArr = npt.NDArray[np.str_]
Stat = Callable[[StrArr, Arr], float]

LABELS: tuple[str, str, str] = ("benign", "imperative_benign", "poisoned")


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str] = LABELS
) -> npt.NDArray[np.int_]:
    idx = {lab: i for i, lab in enumerate(labels)}
    cm = np.zeros((len(labels), len(labels)), dtype=int)
    for t, p in zip(y_true, y_pred, strict=True):
        cm[idx[t], idx[p]] += 1
    return cm


def binary_counts(
    y_true: Sequence[str], y_score: Sequence[float], threshold: float, positive: str = "poisoned"
) -> tuple[int, int, int, int]:
    yt: npt.NDArray[np.bool_] = np.asarray([t == positive for t in y_true], dtype=bool)
    yp: npt.NDArray[np.bool_] = np.asarray(y_score, dtype=float) >= threshold
    tp = int(np.sum(yt & yp))
    fp = int(np.sum(~yt & yp))
    fn = int(np.sum(yt & ~yp))
    tn = int(np.sum(~yt & ~yp))
    return tp, fp, fn, tn


def _safe_div(n: float, d: float) -> float:
    return n / d if d else 0.0


def precision_recall_fpr(
    y_true: Sequence[str], y_score: Sequence[float], threshold: float, positive: str = "poisoned"
) -> tuple[float, float, float]:
    tp, fp, fn, tn = binary_counts(y_true, y_score, threshold, positive)
    return _safe_div(tp, tp + fp), _safe_div(tp, tp + fn), _safe_div(fp, fp + tn)


def t_low_for_recall(
    y_true: Sequence[str], y_score: Sequence[float], target: float = 1.0, positive: str = "poisoned"
) -> float:
    """Highest threshold at which recall of `positive` is >= target."""
    pos = sorted((s for t, s in zip(y_true, y_score, strict=True) if t == positive), reverse=True)
    if not pos:
        raise ValueError("no positive examples")
    k = max(1, math.ceil(target * len(pos)))
    return float(pos[k - 1])


def t_high_for_precision(
    y_true: Sequence[str],
    y_score: Sequence[float],
    target: float = 0.95,
    positive: str = "poisoned",
) -> float:
    """Lowest threshold at which precision of `positive` is >= target; > 1.0 if unattainable."""
    best = 1.0 + 1e-9
    for t in sorted({float(s) for s in y_score}, reverse=True):
        p, _, _ = precision_recall_fpr(y_true, y_score, t, positive)
        if p >= target:
            best = t
        else:
            break
    return best


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str] = LABELS) -> float:
    cm = confusion_matrix(y_true, y_pred, labels)
    f1s: list[float] = []
    for i in range(len(labels)):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        p, r = _safe_div(tp, tp + fp), _safe_div(tp, tp + fn)
        f1s.append(_safe_div(2 * p * r, p + r))
    return float(np.mean(f1s))


def average_precision(
    y_true: Sequence[str], y_score: Sequence[float], positive: str = "poisoned"
) -> float:
    order = np.argsort(-np.asarray(y_score, dtype=float), kind="stable")
    yt = np.asarray([t == positive for t in y_true])[order]
    n_pos = int(yt.sum())
    if n_pos == 0:
        return 0.0
    hits, ap = 0, 0.0
    for i, h in enumerate(yt, start=1):
        if h:
            hits += 1
            ap += hits / i
    return ap / n_pos


def expected_calibration_error(
    probs: Arr, y_true_idx: npt.NDArray[np.int_], n_bins: int = 15
) -> float:
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    acc = (pred == y_true_idx).astype(float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:], strict=True):
        m = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if m.any():
            ece += m.mean() * abs(acc[m].mean() - conf[m].mean())
    return float(ece)


def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    labels = sorted(set(a) | set(b))
    cm = confusion_matrix(list(a), list(b), labels).astype(float)
    n = cm.sum()
    po = np.trace(cm) / n
    pe = float(np.sum(cm.sum(axis=0) * cm.sum(axis=1)) / (n * n))
    return 1.0 if pe == 1.0 else float((po - pe) / (1 - pe))


def gwet_ac1(a: Sequence[str], b: Sequence[str]) -> float:
    """Gwet's AC1: prevalence-robust chance correction. Reported per class beside kappa (M8)."""
    labels = sorted(set(a) | set(b))
    cm = confusion_matrix(list(a), list(b), labels).astype(float)
    n = cm.sum()
    q = len(labels)
    p_obs = float(np.trace(cm) / n)
    if q == 1:
        return 1.0
    pi = (cm.sum(axis=0) + cm.sum(axis=1)) / (2.0 * n)
    p_e = float(np.sum(pi * (1.0 - pi)) / (q - 1))
    return 1.0 if p_e == 1.0 else float((p_obs - p_e) / (1.0 - p_e))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial p-value on the discordant counts of a paired comparison.

    `b` = rows Sieve got right and the baseline got wrong; `c` = the reverse. Under the null
    each discordant row is a fair coin, so p = 2 * P(X <= min(b, c)) with X ~ Bin(b + c, 0.5),
    clipped at 1.0. Used for M2 against the Cisco YARA baseline.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2.0**n)
    return min(1.0, 2.0 * tail)


def bootstrap_ci(
    stat: Stat,
    y_true: Sequence[str],
    y_score: Sequence[float],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Row-level bootstrap. Only where there is no cluster structure; prefer the cluster form."""
    rng = np.random.default_rng(seed)
    yt: StrArr = np.asarray(y_true)
    ys: Arr = np.asarray(y_score, dtype=float)
    point = stat(yt, ys)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(yt), len(yt))
        vals.append(stat(yt[idx], ys[idx]))
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(point), float(lo), float(hi)


def _group_index(groups: Sequence[str]) -> tuple[list[str], dict[str, npt.NDArray[np.int_]]]:
    keys = sorted(set(groups))
    ga = np.asarray(groups)
    return keys, {k: np.flatnonzero(ga == k) for k in keys}


def cluster_bootstrap_ci(
    stat: Stat,
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Bootstrap that resamples GROUPS (servers) with replacement, keeping each group whole.

    Rows within one server are correlated (shared author, shared register), so a row-level
    bootstrap understates the interval. Every published CI uses this (PRD §4.3).
    """
    rng = np.random.default_rng(seed)
    yt: StrArr = np.asarray(y_true)
    ys: Arr = np.asarray(y_score, dtype=float)
    keys, members = _group_index(groups)
    point = stat(yt, ys)
    vals: list[float] = []
    for _ in range(n_boot):
        picked = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([members[keys[p]] for p in picked])
        vals.append(stat(yt[idx], ys[idx]))
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(point), float(lo), float(hi)


def t_low_for_recall_lcb(
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    target: float = 0.98,
    n_boot: int = 1000,
    seed: int = 0,
    positive: str = "poisoned",
) -> float:
    """Highest threshold whose cluster-bootstrap 95% LOWER BOUND of recall is >= target.

    Candidates are the distinct positive scores, descending: no threshold between two
    positive scores changes recall, so nothing else can be optimal. Returns the first
    candidate whose lower bound clears `target`; falls back to the lowest candidate.
    """
    pos = {float(s) for t, s in zip(y_true, y_score, strict=True) if t == positive}
    cands = sorted(pos, reverse=True)
    if not cands:
        raise ValueError("no positive examples")

    def recall_at(threshold: float) -> Stat:
        def stat(yt: StrArr, ys: Arr) -> float:
            return precision_recall_fpr(list(yt), list(ys), threshold, positive)[1]

        return stat

    for t in cands:
        _, lo, _ = cluster_bootstrap_ci(
            recall_at(t), y_true, y_score, groups, n_boot=n_boot, seed=seed
        )
        if lo >= target:
            return t
    return cands[-1]


def paired_bootstrap_diff(
    y_true: Sequence[str],
    score_a: Sequence[float],
    threshold_a: float,
    score_b: Sequence[float],
    threshold_b: float,
    groups: Sequence[str],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
    positive: str = "poisoned",
) -> tuple[float, float, float]:
    """Cluster bootstrap of FPR(a) - FPR(b) on the SAME rows, each system at its own threshold."""
    rng = np.random.default_rng(seed)
    yt: StrArr = np.asarray(y_true)
    sa: Arr = np.asarray(score_a, dtype=float)
    sb: Arr = np.asarray(score_b, dtype=float)
    keys, members = _group_index(groups)

    def diff(i: npt.NDArray[np.int_]) -> float:
        fa = precision_recall_fpr(list(yt[i]), list(sa[i]), threshold_a, positive)[2]
        fb = precision_recall_fpr(list(yt[i]), list(sb[i]), threshold_b, positive)[2]
        return fa - fb

    point = diff(np.arange(len(yt)))
    vals: list[float] = []
    for _ in range(n_boot):
        picked = rng.integers(0, len(keys), len(keys))
        vals.append(diff(np.concatenate([members[keys[p]] for p in picked])))
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(point), float(lo), float(hi)
