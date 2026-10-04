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

FloatArray = npt.NDArray[np.float64]


class RealignError(RuntimeError):
    """The stored rows cannot be matched one-to-one to the re-scored rows."""


def log_softmax(logits: npt.NDArray[Any]) -> FloatArray:
    x = np.asarray(logits, dtype=np.float64)
    shifted = x - x.max(axis=1, keepdims=True)
    return np.asarray(
        shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True)), dtype=np.float64
    )


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


def realign_fold(
    fold_dir: Path,
    dataset_ids: Sequence[str],
    dataset_labels: npt.NDArray[Any],
    score: Callable[[], npt.NDArray[Any]],
    *,
    max_distance: float = MAX_DISTANCE,
) -> dict[str, Any]:
    """Rewrite `fold_dir/oof_ids.json` in stored-row order. `score()` returns logits (or
    log-probabilities) for `dataset_ids`, in that order."""
    stored = np.load(fold_dir / "oof_logits.npy")
    stored_labels = np.load(fold_dir / "oof_labels.npy")
    perm = match_rows(
        log_softmax(stored),
        stored_labels,
        log_softmax(score()),
        np.asarray(dataset_labels),
        max_distance=max_distance,
    )
    ids = [str(dataset_ids[j]) for j in perm]
    before = json.loads((fold_dir / "oof_ids.json").read_text(encoding="utf-8"))
    (fold_dir / "oof_ids.json").write_text(
        json.dumps(ids, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    moved = sum(a != b for a, b in zip(before, ids, strict=True))
    return {"fold_dir": fold_dir.as_posix(), "rows": len(ids), "moved": moved}


def _committed_findings(sha: str = "HEAD") -> Path:
    """The YARA findings the scanner-features ablation trained on, as committed."""
    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "show", f"{sha}:bench/results/baseline_raw.json"],  # noqa: S607
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - outlives the function on purpose
        "w", suffix=".json", delete=False, encoding="utf-8", newline="\n"
    )
    handle.write(out)
    handle.close()
    return Path(handle.name)


def realign_artefact(
    model_dir: Path, corpus: Path, *, max_distance: float = MAX_DISTANCE
) -> list[dict[str, Any]]:
    """Every fold of one `models/<family>-s<seed>` artefact, then its pooled ids."""
    from sieve.model.data import build_splits, load_corpus, load_scanner_findings
    from sieve.model.infer import TorchClassifier
    from sieve.protocols import Thresholds

    metrics = json.loads((model_dir / "fold0" / "metrics.json").read_text(encoding="utf-8"))
    mode: Literal["full", "description_only"] = (
        "description_only" if metrics.get("mode") == "description_only" else "full"
    )
    two_class = int(metrics.get("num_labels") or 3) == 2
    findings = (
        load_scanner_findings(_committed_findings())
        if "scanner" in str(metrics.get("profile") or "")
        else None
    )
    records = load_corpus(corpus)
    unit = Thresholds(0.5, 0.5, 1.0, 1.0, 1.0, 0.0)  # temperature 1: log-probs, not calibrated
    reports = []
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

        report = realign_fold(fold_dir, list(oof["id"]), labels, score, max_distance=max_distance)
        log.info("%s: %d of %d ids moved", fold_dir, report["moved"], report["rows"])
        reports.append(report)

    pooled: list[str] = []
    blocks = []
    for k in FOLDS:
        fold_dir = model_dir / f"fold{k}"
        pooled += json.loads((fold_dir / "oof_ids.json").read_text(encoding="utf-8"))
        blocks.append(np.load(fold_dir / "oof_logits.npy").astype(np.float32))
    if (model_dir / "oof_logits.npy").exists():
        root = np.load(model_dir / "oof_logits.npy")
        if not np.array_equal(root, np.concatenate(blocks)):
            raise RealignError(f"{model_dir}/oof_logits.npy is not its folds concatenated")
        (model_dir / "oof_ids.json").write_text(
            json.dumps(pooled, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    return reports


def main(argv: Sequence[str] | None = None) -> list[dict[str, Any]]:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.realign_oof")
    parser.add_argument("model_dirs", type=Path, nargs="+")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--max-distance", type=float, default=MAX_DISTANCE)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    out = []
    for model_dir in ns.model_dirs:
        out += realign_artefact(model_dir, ns.corpus, max_distance=ns.max_distance)
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":  # pragma: no cover
    main()
