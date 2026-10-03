"""Pure metric functions over NumPy arrays. 100% covered. Shared by baseline and model lanes."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

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
    min_tp: int = 5,
) -> float:
    """min{t : precision(t) >= target and TP(t) >= min_tp} over every distinct score.

    > 1.0 when no threshold qualifies. Training plan D2: the old scan walked down from the top
    and stopped at the first threshold that missed the target, so a single negative above every
    positive returned 1 + 1e-9 (nothing ever auto-confirmed) whatever the rest looked like.
    Precision is not monotone in the threshold, so every candidate is checked; `min_tp` keeps a
    lucky handful of top positives from setting the operating point.
    """
    truth = np.asarray([t == positive for t in y_true], dtype=bool)
    scores: Arr = np.asarray(y_score, dtype=float)
    order = np.argsort(-scores, kind="stable")
    tp = np.cumsum(truth[order])
    predicted = np.arange(1, len(scores) + 1)
    # Ties: a threshold admits every row at its score, so read the counts at each tie's end.
    last = np.r_[scores[order][1:] != scores[order][:-1], True]
    thresholds, tp, predicted = scores[order][last], tp[last], predicted[last]
    ok = (tp >= min_tp) & (tp >= target * predicted)
    return float(thresholds[ok].min()) if ok.any() else 1.0 + 1e-9


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


def macro_f1_present(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str] = LABELS
) -> tuple[float, list[str]]:
    """Macro-F1 over the classes present in `y_true` only, and which classes those were.

    Training plan D5: `macro_f1` averages over every label, so a class absent from a slice
    entered the mean as F1 = 0 and dragged it down for a reason that is not the model's.
    """
    present = [label for label in labels if label in set(y_true)]
    if not present:
        return float("nan"), []
    cm = confusion_matrix(y_true, y_pred, labels)
    f1s = []
    for i, label in enumerate(labels):
        if label not in present:
            continue
        tp = cm[i, i]
        p = _safe_div(tp, cm[:, i].sum())
        r = _safe_div(tp, cm[i, :].sum())
        f1s.append(_safe_div(2 * p * r, p + r))
    return float(np.mean(f1s)), present


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


def auroc(y_true: Sequence[str], y_score: Sequence[float], positive: str = "poisoned") -> float:
    """`positive` vs rest ROC AUC: the Mann-Whitney U with average ranks, so a tie counts half
    and row order never matters (training plan D4). NaN when either class is absent."""
    truth = np.asarray([t == positive for t in y_true], dtype=bool)
    n_pos, n_neg = int(truth.sum()), int((~truth).sum())
    if not n_pos or not n_neg:
        return float("nan")
    scores: Arr = np.asarray(y_score, dtype=float)
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    upper = np.cumsum(counts)
    average_rank = (upper - (counts - 1) / 2.0)[inverse]
    u = average_rank[truth].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def auprc(y_true: Sequence[str], y_score: Sequence[float], positive: str = "poisoned") -> float:
    """`positive` vs rest average precision, Σ (R_k - R_k-1) P_k over distinct thresholds
    descending (sklearn's definition: tied rows enter together). NaN without positives."""
    truth = np.asarray([t == positive for t in y_true], dtype=bool)
    n_pos = int(truth.sum())
    if not n_pos:
        return float("nan")
    scores: Arr = np.asarray(y_score, dtype=float)
    order = np.argsort(-scores, kind="stable")
    sorted_scores = scores[order]
    tp = np.cumsum(truth[order])
    last = np.r_[sorted_scores[1:] != sorted_scores[:-1], True]  # each tie block's end
    tp_at = tp[last].astype(float)
    predicted = (np.flatnonzero(last) + 1).astype(float)
    recall = tp_at / n_pos
    precision = tp_at / predicted
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def cluster_keys(servers: Sequence[str], templates: Sequence[str] | None = None) -> list[str]:
    """The bootstrap's cluster per row: the server, or template × server (MCPTox, §6.6)."""
    if templates is None:
        return list(servers)
    if len(templates) != len(servers):
        raise ValueError(f"templates length {len(templates)} != servers length {len(servers)}")
    return [f"{t}×{s}" for t, s in zip(templates, servers, strict=True)]


def ranking_ci(
    y_true: Sequence[str],
    y_score: Sequence[float],
    clusters: Sequence[str],
    *,
    metric: str = "auroc",
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
    positive: str = "poisoned",
) -> BootstrapResult:
    """AUROC or AUPRC with a cluster-bootstrap CI; replicates missing a class are undefined."""
    fn = {"auroc": auroc, "auprc": auprc}[metric]

    def stat(yt: StrArr, ys: Arr) -> float:
        return fn(list(yt), list(ys), positive)

    return cluster_bootstrap(stat, y_true, y_score, clusters, n_boot=n_boot, seed=seed, alpha=alpha)


ECE_BINS = 15


def reliability_bins(
    confidence: Arr | Sequence[float], outcome: Arr | Sequence[float], n_bins: int = ECE_BINS
) -> list[dict[str, Any]]:
    """Equal-width reliability-diagram bins over [0, 1] (training plan D6).

    `confidence` is a predicted probability and `outcome` the 0/1 event it predicts: the
    top-label probability and whether the argmax was right, or the poisoned column and whether
    the row is poisoned. The first bin is closed on both sides, every other is (lo, hi]. Empty
    bins are kept with `None` means so every curve has the same x-axis.
    """
    conf = np.asarray(confidence, dtype=float)
    hit = np.asarray(outcome, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[dict[str, Any]] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        n = int(m.sum())
        out.append(
            {
                "lo": float(lo),
                "hi": float(hi),
                "n": n,
                "mean_confidence": float(conf[m].mean()) if n else None,
                "observed": float(hit[m].mean()) if n else None,
            }
        )
    return out


def calibration_error(bins: Sequence[dict[str, Any]]) -> float:
    """ECE of `reliability_bins`: the row-weighted mean |observed - mean confidence|."""
    total = sum(b["n"] for b in bins)
    if not total:
        return float("nan")
    return float(
        sum(b["n"] / total * abs(b["observed"] - b["mean_confidence"]) for b in bins if b["n"])
    )


def expected_calibration_error(
    probs: Arr, y_true_idx: npt.NDArray[np.int_], n_bins: int = ECE_BINS
) -> float:
    """Top-label ECE: the argmax's probability against whether the argmax was right."""
    conf = probs.max(axis=1)
    correct = (probs.argmax(axis=1) == y_true_idx).astype(float)
    return calibration_error(reliability_bins(conf, correct, n_bins))


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


def clustered_mcnemar_p(
    baseline_flagged: Sequence[bool], sieve_flagged: Sequence[bool], groups: Sequence[str]
) -> float:
    """Cluster-adjusted McNemar (Durkalski et al., 2003) on paired flags, two-sided.

    X2 = (sum_k d_k)^2 / sum_k d_k^2 with d_k = b_k - c_k, the discordance balance of cluster k
    (b: the baseline flagged and Sieve did not; c: the reverse), referred to chi-square with
    1 df. With one row per cluster it is the uncorrected McNemar statistic; a server whose
    rows all move together counts once, not once per row.
    """
    balance: dict[str, int] = {}
    for base, ours, group in zip(baseline_flagged, sieve_flagged, groups, strict=True):
        balance[group] = balance.get(group, 0) + int(bool(base)) - int(bool(ours))
    d = np.asarray(list(balance.values()), dtype=float)
    denominator = float(np.sum(d**2))
    if denominator == 0.0:
        return 1.0
    statistic = float(np.sum(d)) ** 2 / denominator
    return math.erfc(math.sqrt(statistic / 2.0))


@dataclass(frozen=True)
class FpReduction:
    """M2 (training plan D7): Sieve's pipeline FPR against the baseline's, on the same rows.

    `relative` = (FPR_baseline - FPR_sieve) / FPR_baseline, NaN when the baseline has no false
    positive. Intervals are cluster-bootstrap quantiles over the replicates where the statistic
    is defined; `n_undefined` counts the others. Both McNemar tests use negatives only, the
    rows an FPR is about. The reduction is only meaningful at equal-or-higher recall, so the
    pipeline recall of both systems travels with it and `recall_not_lower` says which case
    this is.
    """

    relative: float
    relative_lo: float
    relative_hi: float
    absolute: float
    absolute_lo: float
    absolute_hi: float
    baseline_fpr: float
    sieve_fpr: float
    n_undefined: int
    mcnemar_p: float
    mcnemar_p_clustered: float
    baseline_recall: float
    sieve_recall: float
    recall_not_lower: bool


def fp_reduction(
    y_true: Sequence[str],
    sieve_flagged: Sequence[bool],
    baseline_flagged: Sequence[bool],
    groups: Sequence[str],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
    positive: str = "poisoned",
) -> FpReduction:
    """M2 with a cluster-bootstrap CI, negatives-only McNemar, and the recall comparison."""
    is_pos = np.asarray(y_true) == positive
    ours = np.asarray(sieve_flagged, dtype=bool)
    base = np.asarray(baseline_flagged, dtype=bool)
    keys, members = _group_index(groups)
    # Per-cluster counts, so a bootstrap replicate is a weighted sum rather than a re-scan.
    neg = np.array([np.sum(~is_pos[members[k]]) for k in keys], dtype=float)
    fp_base = np.array([np.sum(base[members[k]] & ~is_pos[members[k]]) for k in keys], dtype=float)
    fp_ours = np.array([np.sum(ours[members[k]] & ~is_pos[members[k]]) for k in keys], dtype=float)

    def stats(weights: Arr) -> tuple[float, float]:
        negatives = float(weights @ neg)
        if negatives == 0.0:
            return float("nan"), float("nan")
        fpr_base = float(weights @ fp_base) / negatives
        fpr_ours = float(weights @ fp_ours) / negatives
        absolute = fpr_base - fpr_ours
        return (absolute / fpr_base if fpr_base else float("nan")), absolute

    relative, absolute = stats(np.ones(len(keys)))
    rng = np.random.default_rng(seed)
    draws = np.empty((n_boot, 2), dtype=float)
    for b in range(n_boot):
        picked = rng.integers(0, len(keys), len(keys))
        draws[b] = stats(np.bincount(picked, minlength=len(keys)).astype(float))

    def interval(column: Arr) -> tuple[float, float]:
        if np.isnan(column).all():
            return float("nan"), float("nan")
        lo, hi = np.nanquantile(column, [alpha / 2, 1 - alpha / 2])
        return float(lo), float(hi)

    rel_lo, rel_hi = interval(draws[:, 0])
    abs_lo, abs_hi = interval(draws[:, 1])
    negatives = ~is_pos
    b_count = int(np.sum(base & ~ours & negatives))
    c_count = int(np.sum(ours & ~base & negatives))
    neg_groups = [g for g, n in zip(groups, negatives, strict=True) if n]
    base_recall = float(base[is_pos].mean()) if is_pos.any() else float("nan")
    our_recall = float(ours[is_pos].mean()) if is_pos.any() else float("nan")
    total_neg = int(negatives.sum())
    return FpReduction(
        relative=relative,
        relative_lo=rel_lo,
        relative_hi=rel_hi,
        absolute=absolute,
        absolute_lo=abs_lo,
        absolute_hi=abs_hi,
        baseline_fpr=float(np.sum(base & negatives)) / total_neg if total_neg else float("nan"),
        sieve_fpr=float(np.sum(ours & negatives)) / total_neg if total_neg else float("nan"),
        n_undefined=int(np.isnan(draws[:, 0]).sum()),
        mcnemar_p=mcnemar_exact(b_count, c_count),
        mcnemar_p_clustered=clustered_mcnemar_p(base[negatives], ours[negatives], neg_groups),
        baseline_recall=base_recall,
        sieve_recall=our_recall,
        recall_not_lower=bool(our_recall >= base_recall),
    )


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


def _resample_indices(
    rng: np.random.Generator, keys: Sequence[str], members: dict[str, npt.NDArray[np.int_]]
) -> npt.NDArray[np.int_]:
    """One cluster-bootstrap draw: `len(keys)` server keys with replacement, each kept whole."""
    picked = rng.integers(0, len(keys), len(keys))
    return np.concatenate([members[keys[p]] for p in picked])


@dataclass(frozen=True)
class BootstrapResult:
    """A cluster-bootstrap interval. `lo`/`hi` are the alpha/2 and 1 - alpha/2 quantiles of the
    DEFINED replicates; `n_undefined` counts the replicates whose statistic was NaN (e.g. a
    recall drawn from a resample holding no positive), which are excluded, not scored as 0."""

    point: float
    lo: float
    hi: float
    n_undefined: int
    n_boot: int


def cluster_bootstrap(
    stat: Stat,
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Bootstrap that resamples GROUPS (servers) with replacement, keeping each group whole.

    Rows within one server are correlated (shared author, shared register), so a row-level
    bootstrap understates the interval. Every published CI uses this (PRD §4.3). A statistic
    returns NaN where it is undefined on a replicate; those replicates are counted and
    excluded (training plan D3).
    """
    rng = np.random.default_rng(seed)
    yt: StrArr = np.asarray(y_true)
    ys: Arr = np.asarray(y_score, dtype=float)
    keys, members = _group_index(groups)
    point = stat(yt, ys)
    vals = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        idx = _resample_indices(rng, keys, members)
        vals[b] = stat(yt[idx], ys[idx])
    undefined = int(np.isnan(vals).sum())
    if undefined == n_boot:
        lo = hi = float("nan")
    else:
        lo, hi = (float(x) for x in np.nanquantile(vals, [alpha / 2, 1 - alpha / 2]))
    return BootstrapResult(float(point), lo, hi, undefined, n_boot)


def cluster_bootstrap_ci(
    stat: Stat,
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """(point, lo, hi) of `cluster_bootstrap`, for callers that need only the interval."""
    r = cluster_bootstrap(stat, y_true, y_score, groups, n_boot=n_boot, seed=seed, alpha=alpha)
    return r.point, r.lo, r.hi


def t_low_for_recall_lcb(
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    target: float = 0.98,
    n_boot: int = 1000,
    seed: int = 0,
    positive: str = "poisoned",
) -> float:
    """Highest threshold whose cluster-bootstrap lower bound of recall is >= target.

    The float of `t_low_selection`; see there for the bound, the undefined replicates and the
    fallback.
    """
    return t_low_selection(
        y_true, y_score, groups, target=target, n_boot=n_boot, seed=seed, positive=positive
    ).t_low


@dataclass(frozen=True)
class TLowSelection:
    t_low: float
    #: The bootstrap lower bound of recall at `t_low`: the 2.5% quantile of the defined
    #: replicates, i.e. the lower end of a two-sided 95% interval (`quantile`).
    lower_bound: float
    #: True when no candidate cleared the target and `t_low` is the lowest positive score.
    fallback: bool
    #: Replicates that drew no positive at all: recall is undefined there and they are excluded.
    n_undefined: int
    quantile: float = 0.025


def t_low_selection(
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    target: float = 0.98,
    n_boot: int = 1000,
    seed: int = 0,
    positive: str = "poisoned",
) -> TLowSelection:
    """The highest threshold whose bootstrap lower bound of recall clears `target`.

    Candidates are the distinct positive scores, descending: no threshold between two positive
    scores changes recall, so nothing else can be optimal. When none clears, the lowest
    candidate is returned with `fallback=True` rather than silently (training plan D3).
    """
    cands, lower, undefined = _recall_lower_bounds(y_true, y_score, groups, n_boot, seed, positive)
    clears = np.flatnonzero(lower >= target)
    index = int(clears[0]) if len(clears) else len(cands) - 1
    return TLowSelection(
        t_low=float(cands[index]),
        lower_bound=float(lower[index]),
        fallback=not len(clears),
        n_undefined=undefined,
    )


def _recall_lower_bounds(
    y_true: Sequence[str],
    y_score: Sequence[float],
    groups: Sequence[str],
    n_boot: int,
    seed: int,
    positive: str,
    alpha: float = 0.05,
) -> tuple[Arr, Arr, int]:
    """(candidates descending, the 2.5% quantile of recall at each, undefined replicates).

    Training plan D1: the server resample is drawn once per replicate, exactly as
    `cluster_bootstrap_ci` draws it (one fresh generator per call, so every candidate saw the
    same resamples), and recall at every candidate comes from one cumulative sum of the
    positives' multiplicities. Same numbers as one bootstrap per candidate, ~O(n_boot * n).
    """
    yt: StrArr = np.asarray(y_true)
    ys: Arr = np.asarray(y_score, dtype=float)
    pos = np.flatnonzero(yt == positive)
    if not len(pos):
        raise ValueError("no positive examples")
    keys, members = _group_index(groups)
    group_of = np.empty(len(yt), dtype=np.int64)
    for k, key in enumerate(keys):
        group_of[members[key]] = k
    order = pos[np.argsort(-ys[pos], kind="stable")]
    descending = ys[order]
    cands: Arr = np.unique(descending)[::-1]
    # Positives at or above each candidate: a prefix of the descending list.
    upto = len(descending) - np.searchsorted(descending[::-1], cands, side="left")
    pos_groups = group_of[order]

    rng = np.random.default_rng(seed)
    recall = np.empty((n_boot, len(cands)), dtype=float)
    for b in range(n_boot):
        picked = rng.integers(0, len(keys), len(keys))
        cum = np.cumsum(np.bincount(picked, minlength=len(keys))[pos_groups])
        total = cum[-1]
        recall[b] = cum[upto - 1] / total if total else np.nan
    undefined = int(np.isnan(recall[:, 0]).sum())
    if undefined == n_boot:
        return cands, np.full(len(cands), np.nan), undefined
    return cands, np.nanquantile(recall, alpha / 2, axis=0), undefined


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
        vals.append(diff(_resample_indices(rng, keys, members)))
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(point), float(lo), float(hi)


def _sweep(
    is_positive: npt.NDArray[np.bool_], scores: Arr, thresholds: Arr
) -> tuple[Arr, Arr, Arr]:
    """Vectorised precision/recall/fpr at every threshold, for one (truth, score) draw.

    `_safe_div`'s zero-denominator-is-zero convention, applied elementwise: `np.where` picks a
    safe divisor of 1 for the zero-count cells so the division never warns, then the same cells
    are overwritten with 0.0.
    """
    pred = scores[:, None] >= thresholds[None, :]  # (n_rows, n_thresholds)
    pos = is_positive[:, None]
    tp = (pred & pos).sum(axis=0).astype(float)
    fp = (pred & ~pos).sum(axis=0).astype(float)
    fn = (~pred & pos).sum(axis=0).astype(float)
    tn = (~pred & ~pos).sum(axis=0).astype(float)
    precision = np.where(tp + fp > 0, tp / np.where(tp + fp > 0, tp + fp, 1.0), 0.0)
    recall = np.where(tp + fn > 0, tp / np.where(tp + fn > 0, tp + fn, 1.0), 0.0)
    fpr = np.where(fp + tn > 0, fp / np.where(fp + tn > 0, fp + tn, 1.0), 0.0)
    return precision, recall, fpr


def cluster_bootstrap_curve(
    y_true: Sequence[str],
    p_poisoned: Sequence[float],
    groups: Sequence[str],
    thresholds: Arr,
    *,
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
    positive: str = "poisoned",
) -> dict[str, Arr]:
    """Cluster (by server) bootstrap of precision, recall and FPR at every threshold.

    Draws the server resample ONCE per bootstrap replicate and reuses it across all thresholds,
    so the cost is O(n_boot * n_rows) plus a vectorised threshold sweep, not O(n_boot *
    n_thresholds * n_rows). Returns {"precision", "recall", "fpr"}, each shaped
    (3, len(thresholds)) = point, lo, hi.
    """
    rng = np.random.default_rng(seed)
    yt: StrArr = np.asarray(y_true)
    ys: Arr = np.asarray(p_poisoned, dtype=float)
    ts: Arr = np.asarray(thresholds, dtype=float)
    keys, members = _group_index(groups)
    is_pos = yt == positive

    point = _sweep(is_pos, ys, ts)

    boots = np.empty((3, n_boot, len(ts)), dtype=float)
    for b in range(n_boot):
        idx = _resample_indices(rng, keys, members)
        for k, arr in enumerate(_sweep(is_pos[idx], ys[idx], ts)):
            boots[k, b] = arr
        # D3: recall is undefined on a replicate without positives, FPR without negatives.
        if not is_pos[idx].any():
            boots[1, b] = np.nan
        if is_pos[idx].all():
            boots[2, b] = np.nan

    names = ("precision", "recall", "fpr")
    out: dict[str, Arr] = {}
    for k, name in enumerate(names):
        column = boots[k]
        if np.isnan(column).all():
            lo = hi = np.full(len(ts), np.nan)
        else:
            lo, hi = np.nanquantile(column, [alpha / 2, 1 - alpha / 2], axis=0)
        out[name] = np.stack([point[k], lo, hi])
    return out
