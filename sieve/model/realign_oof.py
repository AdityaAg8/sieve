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
import os
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
#: Logits produced under bf16 autocast drift further against an fp32 CPU re-score (0.104 on
#: modernbert-base-s2 fold0, 0.168 on s0; medians near 1e-4). With the identity test below
#: carrying the proof, this is only a cap on what counts as the same prediction at all.
BF16_MAX_DISTANCE = 0.25
#: Two same-label fresh rows closer than this in probability are interchangeable: a swap moves no
#: count-based metric in any slice unless a threshold falls between them, and moves a continuous
#: per-slice metric (AUROC, ECE) by at most this much per swapped row. It can move a row between
#: per-tier or per-source slices, so the guarantee is approximate, not exact.
TIE = 0.02
#: A match must be this much nearer (log-probability L2) than any distinguishable rival.
RATIO = 0.8
#: bf16 drift can leave a few rows genuinely ambiguous between two same-label predictions
#: (modernbert-base-s2 fold0 row 136: 0.2012 against 0.1930). Up to this share of a fold's rows
#: keep the exact assignment as their best guess, are named in the report and in
#: `AMBIGUOUS_FILE` beside the fold's ids; more refuses. fp32 artefacts get no budget.
BF16_AMBIGUOUS_SHARE = 0.02
AMBIGUOUS_FILE = "oof_ids_ambiguous.json"

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


def ambiguity_budget_for(precision: str, rows: int) -> int:
    """How many rows of a `rows`-row fold may stay ambiguous for logits made in `precision`."""
    return int(BF16_AMBIGUOUS_SHARE * rows) if precision == "bf16" else 0


def match_rows(
    stored: FloatArray,
    stored_labels: npt.NDArray[Any],
    fresh: FloatArray,
    fresh_labels: npt.NDArray[Any],
    *,
    max_distance: float = MAX_DISTANCE,
    max_ambiguous: int = 0,
) -> tuple[npt.NDArray[np.int64], list[int]]:
    """(`perm`, ambiguous stored rows) with stored row i == fresh row perm[i]; label-respecting,
    exact assignment. Refuses past `max_ambiguous` rows that fail the identity test."""
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
    fresh_p = np.exp(fresh)
    distances = np.linalg.norm(np.exp(stored[rows]) - fresh_p[cols], axis=1)
    if distances.max() > max_distance:
        raise RealignError(
            f"a stored row's probabilities are {distances.max():.4f} from its match "
            f"(limit {max_distance}): these are not the same predictions"
        )
    # Identity, not just closeness: every match must beat, by RATIO, the nearest same-label
    # fresh row that is *distinguishable* from it. Rows within TIE of each other in probability
    # are interchangeable (see TIE).
    ambiguous: list[int] = []
    first = ""
    for i, j in zip(rows, cols, strict=True):
        rivals = np.flatnonzero(
            (cost[i] < 1e9) & (np.linalg.norm(fresh_p - fresh_p[j], axis=1) > TIE)
        )
        if len(rivals) and cost[i, j] > RATIO * cost[i, rivals].min():
            ambiguous.append(int(i))
            first = first or (
                f"stored row {i}: its match is {cost[i, j]:.4f} away and a "
                f"distinguishable row of the same label {cost[i, rivals].min():.4f}"
            )
    if len(ambiguous) > max_ambiguous:
        raise RealignError(
            f"{len(ambiguous)} stored rows are ambiguous (budget {max_ambiguous}); first, {first}"
        )
    perm = np.empty(len(rows), dtype=np.int64)
    perm[rows] = cols
    return perm, ambiguous


Job = tuple[Path, Sequence[str], npt.NDArray[Any], Callable[[], npt.NDArray[Any]]]


def _aligned_ids(job: Job, max_distance: float, max_ambiguous: int) -> tuple[list[str], list[str]]:
    """The ids in stored-row order, and those of them placed by best guess."""
    fold_dir, dataset_ids, dataset_labels, score = job
    perm, ambiguous = match_rows(
        log_softmax(np.load(fold_dir / "oof_logits.npy")),
        np.load(fold_dir / "oof_labels.npy"),
        log_softmax(score()),
        np.asarray(dataset_labels),
        max_distance=max_distance,
        max_ambiguous=max_ambiguous,
    )
    ids = [str(dataset_ids[j]) for j in perm]
    return ids, [ids[i] for i in ambiguous]


def _write_ids(path: Path, ids: Sequence[str]) -> None:
    """Write through a sibling temp file and `os.replace`, so a crash never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(list(ids), indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _stored_ids(fold_dir: Path, rows: int) -> list[str]:
    """The fold's current ids, refused unless they are a list of `rows` strings."""
    path = fold_dir / "oof_ids.json"
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealignError(f"{path} cannot be read: {exc}") from exc
    if not isinstance(before, list) or len(before) != rows:
        raise RealignError(f"{path} does not hold {rows} ids")
    return [str(i) for i in before]


def realign_folds(
    jobs: Sequence[Job],
    *,
    max_distance: float = MAX_DISTANCE,
    max_ambiguous: int = 0,
    pooled: Path | None = None,
) -> list[dict[str, Any]]:
    """Match every fold first, write after: a refusal anywhere leaves every file untouched.

    Each job is (fold_dir, dataset_ids, dataset_labels, score), `score()` returning logits or
    log-probabilities for `dataset_ids` in that order. `pooled`, when given, is the artefact
    directory whose `oof_logits.npy` must be the folds concatenated; its `oof_ids.json` is
    rewritten to match. `max_ambiguous` is per fold. Every read and check happens before the
    first write; each file is replaced atomically, the pooled one last.
    """
    matched = [_aligned_ids(job, max_distance, max_ambiguous) for job in jobs]
    aligned = [ids for ids, _ in matched]
    befores = [_stored_ids(job[0], len(ids)) for job, ids in zip(jobs, aligned, strict=True)]
    write_pooled = pooled is not None and (pooled / "oof_logits.npy").exists()
    if pooled is not None and not write_pooled:
        log.warning("%s has no pooled oof_logits.npy; only the folds are rewritten", pooled)
    if write_pooled:
        assert pooled is not None
        blocks = [np.load(job[0] / "oof_logits.npy").astype(np.float32) for job in jobs]
        if not np.array_equal(np.load(pooled / "oof_logits.npy"), np.concatenate(blocks)):
            raise RealignError(f"{pooled}/oof_logits.npy is not its folds concatenated")
        if (pooled / "oof_labels.npy").exists():
            labels = np.concatenate([np.load(job[0] / "oof_labels.npy") for job in jobs])
            if not np.array_equal(np.load(pooled / "oof_labels.npy"), labels):
                raise RealignError(f"{pooled}/oof_labels.npy is not its folds concatenated")
    reports = []
    for job, (ids, ambiguous), before in zip(jobs, matched, befores, strict=True):
        _write_ids(job[0] / "oof_ids.json", ids)
        marker = job[0] / AMBIGUOUS_FILE
        if ambiguous:
            _write_ids(marker, ambiguous)
        elif marker.exists():
            marker.unlink()
        moved = sum(a != b for a, b in zip(before, ids, strict=True))
        reports.append(
            {
                "fold_dir": job[0].as_posix(),
                "rows": len(ids),
                "moved": moved,
                "ambiguous": ambiguous,
            }
        )
    if write_pooled:
        assert pooled is not None
        _write_ids(pooled / "oof_ids.json", [i for ids in aligned for i in ids])
    return reports


def realign_fold(
    fold_dir: Path,
    dataset_ids: Sequence[str],
    dataset_labels: npt.NDArray[Any],
    score: Callable[[], npt.NDArray[Any]],
    *,
    max_distance: float = MAX_DISTANCE,
    max_ambiguous: int = 0,
) -> dict[str, Any]:
    """One fold on its own (`realign_folds` with a single job)."""
    job: Job = (fold_dir, dataset_ids, dataset_labels, score)
    return realign_folds([job], max_distance=max_distance, max_ambiguous=max_ambiguous)[0]


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
    precision = str(metrics.get("precision"))
    limit = max_distance if max_distance is not None else tolerance_for(precision)
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
    budget = ambiguity_budget_for(precision, min(len(job[1]) for job in jobs))
    reports = realign_folds(jobs, max_distance=limit, max_ambiguous=budget, pooled=model_dir)
    for report in reports:
        log.info(
            "%s: %d of %d ids moved, %d placed by best guess",
            report["fold_dir"],
            report["moved"],
            report["rows"],
            len(report["ambiguous"]),
        )
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
