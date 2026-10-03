"""Pooled out-of-fold temperature scaling, and the CLI that fills the artefact's thresholds.

Pooling is the safety property: `pool_oof` refuses a set of fold directories whose ids overlap
or whose union is not exactly the dev set, so a calibration can never be fitted on a fold's
own training rows or on a partial corpus (PRD §11.4).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize_scalar  # type: ignore[import-untyped]

from sieve.eval.metrics import expected_calibration_error
from sieve.model import thresholds as th
from sieve.model.data import load_corpus

log = logging.getLogger("sieve.model.calibrate")

TEMPERATURE_BOUNDS = (0.05, 10.0)


class PoolError(ValueError):
    """The fold artefacts do not form one clean pooled out-of-fold prediction set."""


def apply(logits: npt.NDArray[Any], temperature: float) -> npt.NDArray[np.float64]:
    """Softmax of `logits / temperature`, computed in a shift-stable way."""
    scaled = np.asarray(logits, dtype=np.float64) / float(temperature)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return np.asarray(exponentiated / exponentiated.sum(axis=1, keepdims=True), dtype=np.float64)


def _nll(
    logits: npt.NDArray[np.float64], labels: npt.NDArray[np.int_], temperature: float
) -> float:
    probs = apply(logits, temperature)
    picked = probs[np.arange(len(labels)), labels]
    return float(-np.mean(np.log(np.clip(picked, 1e-12, 1.0))))


def fit_temperature(
    logits: npt.NDArray[Any], labels: Sequence[int] | npt.NDArray[np.int_]
) -> float:
    """The single scalar that minimises pooled-OOF NLL. One parameter, so one plot explains it."""
    x = np.asarray(logits, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    result = minimize_scalar(lambda t: _nll(x, y, t), bounds=TEMPERATURE_BOUNDS, method="bounded")
    return float(np.clip(result.x, *TEMPERATURE_BOUNDS))


def _check_head(
    directory: Path, logits: npt.NDArray[np.float64], labels: npt.NDArray[np.int_]
) -> None:
    """The logits' width is the configured head (B11), and every label is one of its ids."""
    try:
        th.head_labels(logits.shape[1])
    except ValueError as error:
        raise PoolError(f"{directory}: {error}") from error
    config_path = directory / th.CONFIG_FILE
    if config_path.exists():
        configured = json.loads(config_path.read_text(encoding="utf-8")).get("num_labels")
        if configured is not None and int(configured) != logits.shape[1]:
            raise PoolError(
                f"{directory}: sieve_config.json says num_labels={configured} but the OOF "
                f"logits have {logits.shape[1]} columns"
            )
    if len(labels) and (labels.min() < 0 or labels.max() >= logits.shape[1]):
        raise PoolError(
            f"{directory}: an OOF label is outside the {logits.shape[1]}-column head's ids"
        )


def pool_oof(
    model_dirs: Sequence[Path], *, expected_ids: Sequence[str] | None = None
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int_], list[str]]:
    """Concatenate the folds' held-out predictions for one (profile, seed)."""
    blocks: list[npt.NDArray[np.float64]] = []
    label_blocks: list[npt.NDArray[np.int_]] = []
    ids: list[str] = []
    for directory in model_dirs:
        for name in ("oof_logits.npy", "oof_labels.npy", "oof_ids.json"):
            if not (directory / name).exists():
                raise PoolError(f"{directory / name} is missing; the fold never finished")
        logits = np.load(directory / "oof_logits.npy").astype(np.float64)
        labels = np.load(directory / "oof_labels.npy").astype(np.int64)
        fold_ids = [str(x) for x in json.loads((directory / "oof_ids.json").read_text("utf-8"))]
        if not (len(logits) == len(labels) == len(fold_ids)):
            raise PoolError(
                f"{directory}: length mismatch — {len(logits)} logits, {len(labels)} labels, "
                f"{len(fold_ids)} ids"
            )
        _check_head(directory, logits, labels)
        if blocks and logits.shape[1] != blocks[0].shape[1]:
            raise PoolError(
                f"{directory} has {logits.shape[1]} logit columns, the first fold "
                f"{blocks[0].shape[1]}; one pool holds one head"
            )
        blocks.append(logits)
        label_blocks.append(labels)
        ids.extend(fold_ids)

    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PoolError(
            f"{len(duplicates)} record(s) appear in more than one fold: {duplicates[:5]}; "
            "the folds are not a partition and the pooled predictions are not out-of-fold"
        )
    if expected_ids is not None:
        expected = set(expected_ids)
        seen = set(ids)
        if expected - seen:
            raise PoolError(
                f"missing {len(expected - seen)} dev record(s): {sorted(expected - seen)[:5]}"
            )
        if seen - expected:
            raise PoolError(
                f"{len(seen - expected)} pooled id(s) are not in the dev set: "
                f"{sorted(seen - expected)[:5]}"
            )
    return np.concatenate(blocks), np.concatenate(label_blocks), ids


# ----------------------------------------------------------------------------------- CLI


def _read_config(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _shared_dir(model_dirs: Sequence[Path], override: Path | None) -> Path:
    if override is not None:
        return override
    config = json.loads((model_dirs[0] / "sieve_config.json").read_text(encoding="utf-8"))
    # The artefact family (C1) names the directory; artefacts from before it fall back to the
    # profile in `model_version`.
    family = config.get("family") or str(config["model_version"]).split("@", 1)[0]
    name = f"{family}-s{config['seed']}"
    # The Makefile nests the folds inside the seed directory (models/<family>-s<seed>/fold<k>);
    # folds kept beside it (models/<family>-f<k>-s<seed>) name it as a sibling instead.
    parent = model_dirs[0].parent
    return parent if parent.name == name else parent / name


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.calibrate")
    parser.add_argument("--dirs", nargs="+", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--recall-target", type=float, default=0.98)
    parser.add_argument("--precision-target", type=float, default=0.95)
    parser.add_argument("--n-boot", type=int, default=1000)
    parser.add_argument("--curve-boot", type=int, default=200)
    parser.add_argument("--curve-points", type=int, default=200)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    records = [r for r in load_corpus(ns.corpus) if r.split == "dev"]
    groups_by_id = {r.id: r.server_name for r in records}
    logits, labels, ids = pool_oof(ns.dirs, expected_ids=list(groups_by_id))
    groups = [groups_by_id[i] for i in ids]

    before = expected_calibration_error(apply(logits, 1.0), labels)
    temperature = fit_temperature(logits, labels)
    probs = apply(logits, temperature)
    after = expected_calibration_error(probs, labels)
    log.info("temperature %.4f; ECE %.4f -> %.4f", temperature, before, after)

    chosen = th.select(
        probs,
        labels,
        groups,
        temperature=temperature,
        recall_target=ns.recall_target,
        precision_target=ns.precision_target,
        n_boot=ns.n_boot,
    )
    spread = th.t_low_spread(probs, labels, groups, target=ns.recall_target, n_boot=ns.n_boot)
    payload = th.threshold_payload(chosen, t_low_spread=spread, ece_before=before, ece_after=after)

    shared = _shared_dir(ns.dirs, ns.out)
    shared.mkdir(parents=True, exist_ok=True)
    # The pooled arrays live beside the pooled thresholds so `sieve.model.select` has one
    # directory per (architecture, seed) to read, rather than re-pooling five folds itself.
    np.save(shared / "oof_logits.npy", logits.astype(np.float32))
    np.save(shared / "oof_labels.npy", labels.astype(np.int64))
    (shared / "oof_ids.json").write_text(
        json.dumps(ids, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    # The operating point is merged into each artefact's own config, never over it: the fold and
    # the training command still name the run that made the weights beside them, and `push`
    # prints the served model's on its card. With no whole-dev model here yet there are no
    # weights to describe, so the pooled config takes fold 0's settings and no command.
    written = shared / th.CONFIG_FILE
    if written.exists():
        served = _read_config(written)
    else:
        served = {**_read_config(ns.dirs[0] / th.CONFIG_FILE), "fold": "pooled"}
        served["training_command"] = None
    if not (shared / "model.safetensors").exists():
        log.warning(
            "%s holds no whole-dev model yet; `make train FOLD=all` would blank these "
            "thresholds, so calibrate after it",
            shared,
        )
    _write_config(written, {**served, **payload})
    # Every per-fold directory gets the operating point too, so a stray fold artefact can never
    # be served with unset thresholds.
    for directory in ns.dirs:
        own = directory / th.CONFIG_FILE
        _write_config(own, {**_read_config(own), **payload})

    th.write_curves(
        probs,
        labels,
        groups,
        ns.results_dir / "curves.json",
        n_thresholds=ns.curve_points,
        n_boot=ns.curve_boot,
    )
    return written


__all__ = ["PoolError", "apply", "fit_temperature", "main", "pool_oof"]

if __name__ == "__main__":  # pragma: no cover
    main()
