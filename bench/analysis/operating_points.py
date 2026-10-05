"""`bench/results/operating_points.json`: a post-hoc, cross-fitted point-recall rule (fix plan P4).

The v1 operating point is the frozen `T_low` (a bootstrap lower bound of recall >= 0.98). This
file asks what a plain point-recall rule would have bought instead, and answers it honestly:
the threshold for fold f is chosen on the OTHER four folds' positives and only then applied to
fold f, so no row is scored at a threshold its own label helped choose. The earlier in-sample
figure ("46% -> 23%") skipped that step and is retired.

Everything comes from dev OOF arrays (`oof_logits.npy`, `oof_ids.json`, the artefact's
temperature), pooled through `pool_oof` with the corpus ids and labels, so a misaligned fold
raises. Nothing here reads the holdout, and nothing here is adopted: v1's thresholds stay the
artefacts' own (`status`). Adopting a rule found this way needs a new sealed set.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench.ablations.run import REAL_SOURCES, dataset_version, load_baseline_rows
from sieve.model import thresholds as th
from sieve.model.calibrate import PoolError, apply, pool_oof
from sieve.types import CorpusRecord

log = logging.getLogger("bench.analysis.operating_points")

OUT_FILE = "operating_points.json"
STATUS = "post-hoc; not adopted for v1"
TARGETS: tuple[float, ...] = (1.0, 0.995, 0.99, 0.98, 0.97, 0.95)
RULE = (
    "For each target recall r and each fold f, the threshold is the floor((1 - r) * n_pos)-th "
    "lowest poisoned score (0-indexed) among the positives of the other folds, applied to fold "
    "f only; recall, misses and the review and dismissal shares are then pooled over all folds."
)
#: `(1 - 0.9) * 10` is 0.9999999999999998; the rule means the exact product, so floor it with
#: a tolerance far below any real (1 - r) * n step.
_FLOOR_TOLERANCE = 1e-9


class OperatingPointError(ValueError):
    """A candidate artefact cannot be read as calibrated dev OOF predictions."""


def crossfit_threshold(other_positive_scores: Sequence[float], target: float) -> float:
    """The floor((1 - target) * n)-th lowest of the other folds' positive scores (0-indexed)."""
    ordered = sorted(other_positive_scores)
    if not ordered:
        raise OperatingPointError("a fold has no positives among the other folds")
    k = math.floor((1.0 - target) * len(ordered) + _FLOOR_TOLERANCE)
    return float(ordered[min(k, len(ordered) - 1)])


def operating_points(
    labels: Sequence[str],
    sources: Sequence[str],
    folds: Sequence[int],
    scores: Sequence[float],
    yara_flagged: Sequence[bool],
    *,
    targets: Sequence[float] = TARGETS,
) -> list[dict[str, Any]]:
    """One row per target: the cross-fitted rule's pooled recall, review and dismissal shares."""
    positive = [label == "poisoned" for label in labels]
    real_negative = [
        not p and source in REAL_SOURCES for p, source in zip(positive, sources, strict=True)
    ]
    yara_fp = [rn and flagged for rn, flagged in zip(real_negative, yara_flagged, strict=True)]
    rows = []
    for target in targets:
        thresholds = {
            fold: crossfit_threshold(
                [s for s, p, f in zip(scores, positive, folds, strict=True) if p and f != fold],
                target,
            )
            for fold in sorted(set(folds))
        }
        review = [s >= thresholds[f] for s, f in zip(scores, folds, strict=True)]
        caught = [r for r, p in zip(review, positive, strict=True) if p]
        real_reviewed = [r for r, rn in zip(review, real_negative, strict=True) if rn]
        yara_reviewed = [r for r, fp in zip(review, yara_fp, strict=True) if fp]
        rows.append(
            {
                "target_recall": target,
                "recall": _share(caught),
                "misses": len(caught) - sum(caught),
                "n_poisoned": len(caught),
                "real_negative_review_share": _share(real_reviewed),
                "n_real_negatives": len(real_reviewed),
                "yara_fp_dismissed": (
                    1.0 - sum(yara_reviewed) / len(yara_reviewed) if yara_reviewed else None
                ),
                "n_yara_fp": len(yara_reviewed),
                "thresholds_per_fold": {str(f): t for f, t in thresholds.items()},
            }
        )
    return rows


def _share(flags: Sequence[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _temperature(model_dir: Path) -> float:
    config = model_dir / th.CONFIG_FILE
    value = (
        json.loads(config.read_text(encoding="utf-8")).get("temperature")
        if config.exists()
        else None
    )
    if value is None:
        raise OperatingPointError(f"{config} has no temperature; calibrate the artefact first")
    return float(value)


def seed_rows(
    model_dir: Path, records: Sequence[CorpusRecord], yara: dict[str, bool]
) -> list[dict[str, Any]]:
    """The rule's rows for one (architecture, seed), from its pooled, id- and label-checked OOF."""
    logits, _, ids = pool_oof(
        [model_dir],
        expected_ids=[r.id for r in records],
        expected_labels={r.id: r.label for r in records},
    )
    scores = th.poisoned_scores(apply(logits, _temperature(model_dir)))
    by_id = {r.id: r for r in records}
    ordered = [by_id[i] for i in ids]
    folds = [r.fold for r in ordered if r.fold is not None]
    if len(folds) != len(ordered):
        raise OperatingPointError("a dev record has no fold; run bench.split.assign first")
    return operating_points(
        [r.label for r in ordered],
        [r.source for r in ordered],
        folds,
        scores,
        [yara.get(r.id, False) for r in ordered],
    )


def _selection(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "selection.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.analysis.operating_points")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument(
        "--candidate",
        type=Path,
        action="append",
        default=None,
        help="a seed directory (repeatable); default: selection.json's winner's directories",
    )
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from sieve.model.data import load_corpus

    selection = _selection(ns.results_dir)
    candidates = ns.candidate or [
        Path(d) for d in selection["per_architecture"][selection["winner"]]["directories"]
    ]
    records = [r for r in load_corpus(ns.corpus) if r.split == "dev"]
    yara = {
        record_id: bool(row.get("flagged"))
        for record_id, row in load_baseline_rows(ns.results_dir / "baseline_raw.json").items()
    }
    payload = {
        "status": STATUS,
        "rule": RULE,
        "dataset_version": dataset_version(ns.corpus),
        "published_dir": selection.get("published_dir"),
        "per_seed": {Path(d).name: seed_rows(Path(d), records, yara) for d in candidates},
    }
    path: Path = ns.results_dir / OUT_FILE
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    log.info("wrote %s", path)
    return path


__all__ = [
    "RULE",
    "STATUS",
    "TARGETS",
    "OperatingPointError",
    "PoolError",
    "crossfit_threshold",
    "main",
    "operating_points",
    "seed_rows",
]

if __name__ == "__main__":  # pragma: no cover
    main()
