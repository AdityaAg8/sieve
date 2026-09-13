"""Threshold selection and the artefact's threshold file. No default threshold lives here.

`T_low` is the highest threshold whose **cluster-bootstrap lower bound** of poisoned recall
clears the target, not the lowest positive score: the second is a one-row statistic that any
relabelled row can move, and it was the v2.0 bug this file exists to avoid (PRD §11.4).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from sieve.eval.metrics import (
    cluster_bootstrap_ci,
    precision_recall_fpr,
    t_high_for_precision,
    t_low_for_recall_lcb,
)
from sieve.protocols import Thresholds
from sieve.types import LABELS

CONFIG_FILE = "sieve_config.json"

REQUIRED_FIELDS: tuple[str, ...] = (
    "temperature",
    "t_low",
    "t_high",
    "val_recall_at_t_low",
    "val_precision_at_t_high",
    "routing_rate",
)

UNCALIBRATED = Thresholds(
    t_low=float("nan"),
    t_high=float("nan"),
    temperature=1.0,
    val_recall_at_t_low=float("nan"),
    val_precision_at_t_high=float("nan"),
    routing_rate=float("nan"),
)
"""No operating point has been chosen yet.

The `Classifier` Protocol requires a `thresholds` attribute, but this lane has no default
threshold (PRD §11.4): a routing decision only ever comes from `select` or `load`. NaN is the
sentinel because it is loud — it survives no comparison and no JSON round trip unnoticed.
"""


class ThresholdsMissing(Exception):  # noqa: N818 - named by the lane brief; other lanes catch it
    """The artefact carries no usable operating point. Serving it would invent one."""


def poisoned_scores(probs: npt.NDArray[np.float64]) -> list[float]:
    """Column 2 of a calibrated `[n, 3]` block, in `sieve.types.LABELS` order."""
    return [float(x) for x in np.asarray(probs, dtype=np.float64)[:, LABELS.index("poisoned")]]


def label_strings(labels: Sequence[int] | npt.NDArray[np.int_]) -> list[str]:
    return [LABELS[int(i)] for i in labels]


def select(
    probs: npt.NDArray[np.float64],
    labels: Sequence[int] | npt.NDArray[np.int_],
    groups: Sequence[str],
    *,
    temperature: float,
    recall_target: float = 0.98,
    precision_target: float = 0.95,
    n_boot: int = 1000,
    seed: int = 0,
) -> Thresholds:
    """The operating point, from pooled OOF calibrated probabilities. `Thresholds` is a contract."""
    truth = label_strings(labels)
    scores = poisoned_scores(probs)
    t_low = t_low_for_recall_lcb(
        truth, scores, groups, target=recall_target, n_boot=n_boot, seed=seed
    )
    t_high = t_high_for_precision(truth, scores, target=precision_target)
    if t_high < t_low:
        # The classifier already clears the precision target below the recall threshold, so the
        # abstain band is empty and nothing routes to the judge. Collapsing t_high onto t_low is
        # the only coherent reading; an inverted band would auto-confirm rows it also dismissed.
        t_high = t_low
    _, recall, _ = precision_recall_fpr(truth, scores, t_low)
    precision, _, _ = precision_recall_fpr(truth, scores, t_high)
    band = np.asarray(scores)
    routing_rate = float(np.mean((band >= t_low) & (band < t_high)))
    return Thresholds(
        t_low=float(t_low),
        t_high=float(t_high),
        temperature=float(temperature),
        val_recall_at_t_low=float(recall),
        val_precision_at_t_high=float(precision),
        routing_rate=routing_rate,
    )


def t_low_spread(
    probs: npt.NDArray[np.float64],
    labels: Sequence[int] | npt.NDArray[np.int_],
    groups: Sequence[str],
    *,
    target: float = 0.98,
    refits: int = 5,
    n_boot: int = 1000,
) -> list[float]:
    """`refits` re-runs of the LCB selection under bootstrap seeds `0..refits-1`.

    Published beside `t_low` so its variability is visible rather than implied. It brackets
    `select`'s `t_low` whenever `select` used a seed in the same range, which is the default.
    """
    truth = label_strings(labels)
    scores = poisoned_scores(probs)
    return [
        float(t_low_for_recall_lcb(truth, scores, groups, target=target, n_boot=n_boot, seed=seed))
        for seed in range(refits)
    ]


def _recall_at(threshold: float) -> Any:
    def stat(truth: Any, scores: Any) -> float:
        return precision_recall_fpr(list(truth), list(scores), threshold)[1]

    return stat


def _fpr_at(threshold: float) -> Any:
    def stat(truth: Any, scores: Any) -> float:
        return precision_recall_fpr(list(truth), list(scores), threshold)[2]

    return stat


def write_curves(
    probs: npt.NDArray[np.float64],
    labels: Sequence[int] | npt.NDArray[np.int_],
    groups: Sequence[str],
    path: Path,
    *,
    n_thresholds: int = 200,
    n_boot: int = 200,
    seed: int = 0,
) -> Path:
    """Recall-vs-threshold and FPR-vs-recall with cluster-bootstrap bounds (PRD §11.4).

    `n_boot` defaults below the 1,000 of a published point estimate on purpose: this is
    `n_thresholds` separate cluster bootstraps, and `metrics.cluster_bootstrap_ci` is a Python
    loop. The operating point itself is always selected at the full 1,000.
    """
    truth = label_strings(labels)
    scores = poisoned_scores(probs)
    grid = np.linspace(0.0, 1.0, n_thresholds)

    recall_rows: list[list[float]] = []
    fpr_rows: list[list[float]] = []
    for threshold in grid:
        t = float(threshold)
        r_point, r_lo, r_hi = cluster_bootstrap_ci(
            _recall_at(t), truth, scores, groups, n_boot=n_boot, seed=seed
        )
        f_point, f_lo, f_hi = cluster_bootstrap_ci(
            _fpr_at(t), truth, scores, groups, n_boot=n_boot, seed=seed
        )
        recall_rows.append([t, r_point, min(r_lo, r_point), max(r_hi, r_point)])
        fpr_rows.append([r_point, f_point, min(f_lo, f_point), max(f_hi, f_point)])

    # Both curves are traced by walking the threshold down, so sort the FPR curve onto its own
    # recall axis. Recall is non-increasing in the threshold by construction; a bootstrap bound
    # is not, so the bounds are clamped around the point above rather than reported crossing it.
    # Ties matter: several thresholds can share a recall while differing in FPR, and the
    # threshold order within a tie runs the wrong way, so order by (recall, FPR).
    fpr_rows.sort(key=lambda row: (row[0], row[1]))
    payload = {"recall_vs_threshold": recall_rows, "fpr_vs_recall": fpr_rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def threshold_payload(
    chosen: Thresholds,
    *,
    t_low_spread: Sequence[float],
    ece_before: float,
    ece_after: float,
) -> dict[str, Any]:
    """The threshold block of `sieve_config.json`, in one place so every writer agrees."""
    return {
        "temperature": chosen.temperature,
        "t_low": chosen.t_low,
        "t_high": chosen.t_high,
        "t_low_spread": [float(x) for x in t_low_spread],
        "ece_before": float(ece_before),
        "ece_after": float(ece_after),
        "routing_rate": chosen.routing_rate,
        "val_recall_at_t_low": chosen.val_recall_at_t_low,
        "val_precision_at_t_high": chosen.val_precision_at_t_high,
    }


def load(model_dir: Path) -> Thresholds:
    """Read the artefact's operating point. Raises rather than inventing one."""
    path = model_dir / CONFIG_FILE
    if not path.exists():
        raise ThresholdsMissing(f"{path} does not exist; the artefact was never calibrated")
    config = json.loads(path.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_FIELDS if config.get(f) is None]
    if missing:
        raise ThresholdsMissing(
            f"{path} has no value for {', '.join(missing)}; run "
            f"`python -m sieve.model.calibrate --dirs ...` before serving this artefact"
        )
    return Thresholds(
        t_low=float(config["t_low"]),
        t_high=float(config["t_high"]),
        temperature=float(config["temperature"]),
        val_recall_at_t_low=float(config["val_recall_at_t_low"]),
        val_precision_at_t_high=float(config["val_precision_at_t_high"]),
        routing_rate=float(config["routing_rate"]),
    )
