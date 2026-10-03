"""`make sweep PROFILE=... SEEDS="0 1 2"` (training plan C2).

Per seed: the five CV folds, the median best epoch, the whole-dev model stopped there (B9), then
pooled-OOF calibration, all into `models/<family>-s<seed>` (C1).

* It stops at the first failed step.
* It resumes: a fold is skipped when its `metrics.json` was made from this corpus at this commit.
* It never mixes evidence: a fold from another corpus or commit is a refusal, not a retrain,
  because pooling it with new folds would publish numbers no single run produced.
* It refuses a busy GPU before every training step (`bench.gpu`), unless `--force`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bench import gpu
from sieve.model.family import CONFIG, family
from sieve.model.version import git_sha

log = logging.getLogger("bench.sweep")

FOLDS = range(5)
Runner = Callable[[Sequence[str]], None]


class StepFailedError(RuntimeError):
    """A training or calibration step exited non-zero; the sweep stops there."""


class SweepRefusedError(RuntimeError):
    """Running would mix evidence or share a busy GPU."""


def run_module(argv: Sequence[str]) -> None:
    """`python -m <argv>` in this interpreter's environment."""
    result = subprocess.run([sys.executable, "-m", *argv], check=False)  # noqa: S603 - fixed argv
    if result.returncode != 0:
        raise StepFailedError(f"python -m {' '.join(argv)} exited {result.returncode}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _metrics(directory: Path) -> dict[str, Any] | None:
    path = directory / "metrics.json"
    if not path.exists():
        return None
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _check_evidence(directory: Path, corpus_sha: str, head: str) -> None:
    metrics = _metrics(directory)
    if metrics is None:
        return
    for key, want in (("corpus_sha256", corpus_sha), ("git_sha", head)):
        if metrics.get(key) != want:
            raise SweepRefusedError(
                f"{directory / 'metrics.json'} has {key}={metrics.get(key)} but this sweep has "
                f"{want}; pooling it with new folds would mix evidence. Move that directory "
                "aside (or restore its corpus/commit) and re-run."
            )


def _gate(env: Mapping[str, str], *, force: bool, idle_mib: int) -> None:
    if force or gpu.cuda_hidden(env):
        return
    state = gpu.query()
    reason = None if state is None else gpu.busy_reason(state, idle_mib=idle_mib)
    if reason:
        raise SweepRefusedError(f"{reason}. Wait for it to finish (or pass --force).")


def sweep(
    profile: str,
    *,
    seeds: Sequence[int],
    corpus: Path,
    models_root: Path = Path("models"),
    results_dir: Path = Path("bench/results"),
    config: Path = CONFIG,
    runner: Runner = run_module,
    env: Mapping[str, str] | None = None,
    force: bool = False,
    idle_mib: int = gpu.IDLE_MIB,
) -> list[Path]:
    env = os.environ if env is None else env
    corpus_sha = _sha256(corpus)
    head = git_sha(short=False)
    name = family(config, profile)
    seed_dirs = [models_root / f"{name}-s{seed}" for seed in seeds]

    # Every refusal before the first step, so a stale directory never costs a fold of GPU time.
    for directory in seed_dirs:
        for sub in (directory, *(directory / f"fold{k}" for k in FOLDS)):
            _check_evidence(sub, corpus_sha, head)

    def train(fold: str, seed: int, out: Path, *extra: str) -> None:
        _gate(env, force=force, idle_mib=idle_mib)
        log.info("train %s fold %s seed %s -> %s", profile, fold, seed, out)
        runner(["sieve.model.train", "--config", str(config), "--profile", profile,
                "--fold", fold, "--seed", str(seed), "--corpus", str(corpus),
                "--out", str(out), *extra])  # fmt: skip

    for seed, directory in zip(seeds, seed_dirs, strict=True):
        folds = [directory / f"fold{k}" for k in FOLDS]
        for k, fold_dir in zip(FOLDS, folds, strict=True):
            if _metrics(fold_dir) is not None:
                log.info(
                    "skip fold %d seed %d: %s matches this corpus and commit", k, seed, fold_dir
                )
                continue
            train(str(k), seed, fold_dir)

        recorded = [(_metrics(d) or {}).get("best_epoch") for d in folds]
        best = [b for b in recorded if isinstance(b, int)]
        if len(best) != len(recorded):
            raise SweepRefusedError(f"a fold of seed {seed} recorded no best_epoch: {recorded}")
        stop = int(statistics.median(best))
        log.info("seed %d best epochs %s -> --fold all --epochs %d", seed, best, stop)

        whole = _metrics(directory)
        if whole is None or whole.get("stop_after_epochs") != stop:
            train("all", seed, directory, "--epochs", str(stop))
        else:
            log.info("skip the whole-dev model of seed %d: already stopped at epoch %d", seed, stop)

        runner(["sieve.model.calibrate", "--dirs", *map(str, folds), "--corpus", str(corpus),
                "--out", str(directory), "--results-dir", str(results_dir)])  # fmt: skip
    return seed_dirs


def main(argv: Sequence[str] | None = None) -> list[Path]:
    parser = argparse.ArgumentParser(prog="python -m bench.sweep")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seeds", default="0 1 2", help='e.g. "0 1 2"')
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--models-root", type=Path, default=Path("models"))
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument("--force", action="store_true", help="skip the busy-GPU refusal")
    parser.add_argument("--idle-mib", type=int, default=gpu.IDLE_MIB)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    if ns.corpus is None:
        from sieve.model.train import default_corpus

        ns.corpus = default_corpus(ns.config)
    seeds = [int(s) for s in ns.seeds.replace(",", " ").split()]
    try:
        return sweep(
            ns.profile, seeds=seeds, corpus=ns.corpus, models_root=ns.models_root,
            results_dir=ns.results_dir, config=ns.config, force=ns.force, idle_mib=ns.idle_mib,
        )  # fmt: skip
    except (StepFailedError, SweepRefusedError) as error:
        raise SystemExit(f"sweep stopped: {error}") from error


if __name__ == "__main__":  # pragma: no cover
    main()
