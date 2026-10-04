"""Put v1's OOF ids back onto the rows they describe (ADR-009).

transformers 4.57 length-groups `trainer.predict` whenever `group_by_length` is on, so v1's first
sweep saved each fold's held-out logits in a shuffled order beside an `oof_ids.json` in dataset
order. Every metric that reads logits with their own labels (calibration, thresholds, selection,
best epoch) never noticed. Every metric that joins a prediction to its record by id did.

The repair re-scores each fold's held-out rows with that fold's own saved model, in dataset
order, and matches every stored row to the fresh row with the same label and the nearest
log-probabilities: a bijection, solved exactly, refused unless every match is close. Only
`oof_ids.json` is rewritten. The logits and labels stay byte-identical, so the calibration and
the thresholds the sealed evaluation used do not move. The artefact's pooled `oof_ids.json`
(the folds concatenated, `sieve.model.calibrate`) is rewritten to match.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

log = logging.getLogger("sieve.model.realign_oof")

FOLDS = range(5)
#: Largest L2 distance between a stored and a re-scored *probability* row. Rows are matched on
#: log-probabilities, where they separate best, but accepted on probabilities: bf16 autocast
#: (ModernBERT) moves a near-certain row's tail log-probability by 0.5 while its probabilities
#: move by under 0.025 (measured on modernbert-base-s0 fold0); a wrong row moves by far more.
MAX_DISTANCE = 0.05
#: Logits produced under bf16 autocast drift further against an fp32 CPU re-score: up to 0.104
#: in probability on modernbert-base-s2 fold0 (median 0.00016, 4 of 212 rows above 0.05).
BF16_MAX_DISTANCE = 0.15

FloatArray = npt.NDArray[np.float64]


class RealignError(RuntimeError):
    """The stored rows cannot be matched one-to-one to the re-scored rows."""


def log_softmax(logits: npt.NDArray[Any]) -> FloatArray:
    x = np.asarray(logits, dtype=np.float64)
    shifted = x - x.max(axis=1, keepdims=True)
    return np.asarray(
        shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True)), dtype=np.float64
    )


def tolerance_for(precision: str) -> float:
    """The acceptance limit for logits produced in `precision` (`metrics.json`)."""
    return BF16_MAX_DISTANCE if precision == "bf16" else MAX_DISTANCE


def match_rows(
    stored: FloatArray,
    stored_labels: npt.NDArray[Any],
    fresh: FloatArray,
    fresh_labels: npt.NDArray[Any],
    *,
    max_distance: float = MAX_DISTANCE,
) -> npt.NDArray[np.int64]:
    """`perm` with stored row i == fresh row perm[i]; label-respecting, exact assignment."""
    from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

    if stored.shape != fresh.shape:
        raise RealignError(f"{stored.shape} stored rows against {fresh.shape} fresh rows")
    if sorted(np.asarray(stored_labels).tolist()) != sorted(np.asarray(fresh_labels).tolist()):
        raise RealignError("the stored and re-scored rows hold different labels")
    cost = np.linalg.norm(stored[:, None, :] - fresh[None, :, :], axis=2)
    cost[np.asarray(stored_labels)[:, None] != np.asarray(fresh_labels)[None, :]] = 1e9
    rows, cols = linear_sum_assignment(cost)
    if (cost[rows, cols] >= 1e9).any():
        raise RealignError("no label-respecting one-to-one match exists")
    distances = np.linalg.norm(np.exp(stored[rows]) - np.exp(fresh[cols]), axis=1)
    if distances.max() > max_distance:
        raise RealignError(
            f"a stored row's probabilities are {distances.max():.4f} from its match "
            f"(limit {max_distance}): these are not the same predictions"
        )
    perm = np.empty(len(rows), dtype=np.int64)
    perm[rows] = cols
    return perm


Job = tuple[Path, Sequence[str], npt.NDArray[Any], Callable[[], npt.NDArray[Any]]]


def _aligned_ids(job: Job, max_distance: float) -> list[str]:
    fold_dir, dataset_ids, dataset_labels, score = job
    perm = match_rows(
        log_softmax(np.load(fold_dir / "oof_logits.npy")),
        np.load(fold_dir / "oof_labels.npy"),
        log_softmax(score()),
        np.asarray(dataset_labels),
        max_distance=max_distance,
    )
    return [str(dataset_ids[j]) for j in perm]


def _write_ids(path: Path, ids: Sequence[str]) -> None:
    path.write_text(json.dumps(list(ids), indent=2) + "\n", encoding="utf-8", newline="\n")


def realign_folds(
    jobs: Sequence[Job], *, max_distance: float = MAX_DISTANCE, pooled: Path | None = None
) -> list[dict[str, Any]]:
    """Match every fold first, write after: a refusal anywhere leaves every file untouched.

    Each job is (fold_dir, dataset_ids, dataset_labels, score), `score()` returning logits or
    log-probabilities for `dataset_ids` in that order. `pooled`, when given, is the artefact
    directory whose `oof_logits.npy` must be the folds concatenated; its `oof_ids.json` is
    rewritten to match.
    """
    aligned = [_aligned_ids(job, max_distance) for job in jobs]
    if pooled is not None and (pooled / "oof_logits.npy").exists():
        blocks = [np.load(job[0] / "oof_logits.npy").astype(np.float32) for job in jobs]
        if not np.array_equal(np.load(pooled / "oof_logits.npy"), np.concatenate(blocks)):
            raise RealignError(f"{pooled}/oof_logits.npy is not its folds concatenated")
    reports = []
    for job, ids in zip(jobs, aligned, strict=True):
        path = job[0] / "oof_ids.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        _write_ids(path, ids)
        moved = sum(a != b for a, b in zip(before, ids, strict=True))
        reports.append({"fold_dir": job[0].as_posix(), "rows": len(ids), "moved": moved})
    if pooled is not None and (pooled / "oof_logits.npy").exists():
        _write_ids(pooled / "oof_ids.json", [i for ids in aligned for i in ids])
    return reports


def realign_fold(
    fold_dir: Path,
    dataset_ids: Sequence[str],
    dataset_labels: npt.NDArray[Any],
    score: Callable[[], npt.NDArray[Any]],
    *,
    max_distance: float = MAX_DISTANCE,
) -> dict[str, Any]:
    """One fold on its own (`realign_folds` with a single job)."""
    job: Job = (fold_dir, dataset_ids, dataset_labels, score)
    return realign_folds([job], max_distance=max_distance)[0]


def _findings_at(sha: str) -> dict[str, Any]:
    """The YARA findings as committed at `sha`: the commit the scanner-features artefact was
    trained at, so a later `make bench` regenerating `baseline_raw.json` cannot change them."""
    from sieve.model.data import load_scanner_findings

    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "show", f"{sha}:bench/results/baseline_raw.json"],  # noqa: S607
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    with tempfile.TemporaryDirectory(prefix="sieve-realign-") as work:
        path = Path(work) / "baseline_raw.json"
        path.write_text(out, encoding="utf-8", newline="\n")
        return dict(load_scanner_findings(path))


def realign_artefact(
    model_dir: Path, corpus: Path, *, max_distance: float | None = None
) -> list[dict[str, Any]]:
    """Every fold of one `models/<family>-s<seed>` artefact, then its pooled ids."""
    from sieve.model.data import build_splits, load_corpus
    from sieve.model.infer import TorchClassifier
    from sieve.protocols import Thresholds

    metrics = json.loads((model_dir / "fold0" / "metrics.json").read_text(encoding="utf-8"))
    limit = max_distance if max_distance is not None else tolerance_for(metrics.get("precision"))
    mode: Literal["full", "description_only"] = (
        "description_only" if metrics.get("mode") == "description_only" else "full"
    )
    two_class = int(metrics.get("num_labels") or 3) == 2
    findings = (
        _findings_at(str(metrics["git_sha"]))
        if "scanner" in str(metrics.get("profile") or "")
        else None
    )
    records = load_corpus(corpus)
    unit = Thresholds(0.5, 0.5, 1.0, 1.0, 1.0, 0.0)  # temperature 1: log-probs, not calibrated
    jobs: list[Job] = []
    for k in FOLDS:
        fold_dir = model_dir / f"fold{k}"
        oof = build_splits(records, fold=k, mode=mode, scanner_findings_by_id=findings)["oof"]
        labels = np.asarray(
            [int(x == 2) if two_class else int(x) for x in oof["label"]], dtype=np.int64
        )
        texts = [str(t) for t in oof["text"]]

        def score(fold_dir: Path = fold_dir, texts: list[str] = texts) -> FloatArray:
            probs = TorchClassifier(fold_dir, thresholds=unit).predict_proba(texts)
            return np.log(np.clip(probs, 1e-300, None))

        jobs.append((fold_dir, list(oof["id"]), labels, score))
    reports = realign_folds(jobs, max_distance=limit, pooled=model_dir)
    for report in reports:
        log.info("%s: %d of %d ids moved", report["fold_dir"], report["moved"], report["rows"])
    return reports


def main(argv: Sequence[str] | None = None) -> list[dict[str, Any]]:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.realign_oof")
    parser.add_argument("model_dirs", type=Path, nargs="+")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--max-distance", type=float, default=None)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    import torch

    torch.set_num_threads(8)  # the machine is shared with a GPU trainer's data loading
    out = []
    for model_dir in ns.model_dirs:
        out += realign_artefact(model_dir, ns.corpus, max_distance=ns.max_distance)
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":  # pragma: no cover
    main()
