"""`make gpu-bench` (training plan C3, §6.4, §13 step 7).

Measures every speed variant on real length-grouped batches, picks one per architecture by the
pre-registered rule, and measures the residual CUDA non-determinism the model card discloses.

* Refuses unless the card is free (`bench.gpu`; `--force` overrides) and CUDA is visible.
* Each variant: fold 0, seed 0, `--max-steps 60` in its own process (so `max_memory_allocated`
  is that variant's alone); the first 10 steps are warm-up, the next 50 are timed.
* The choice, per architecture: the variant with the lowest s/epoch whose peak allocated memory
  is at most 11.0 GiB. Variants change speed and memory only, never the effective batch (16).
* Non-determinism: fold 0 seed 0 for 2 epochs, twice, with the chosen variant of each
  architecture; the OOF macro-F1 gap and the largest logit difference are recorded.

Writes `bench/results/gpu_bench.json`. Never touches another process; it only refuses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from bench import gpu
from bench.sweep import run_module
from sieve.model.family import CONFIG, family
from sieve.model.version import git_sha

WARMUP = 10
TIMED = 50
PEAK_LIMIT_GIB = 11.0
REPEAT_EPOCHS = 2

#: The speed variants the choice is made between, per architecture (§6.4).
CANDIDATES: dict[str, tuple[str, ...]] = {
    "deberta-v3-base": ("deberta", "deberta-nockpt"),
    "modernbert-base": ("modernbert", "modernbert-acc"),
}
#: Measured so the long-context ablation's memory is known before §13 step 10; never chosen.
ABLATION_VARIANTS: tuple[str, ...] = ("modernbert-1024",)
VARIANTS_IN_ORDER: tuple[str, ...] = (
    *(p for profiles in CANDIDATES.values() for p in profiles),
    *ABLATION_VARIANTS,
)

Runner = Callable[[Sequence[str]], None]


class BenchError(RuntimeError):
    """A measurement is unusable; the profile choice would be made on nothing."""


def refusal(
    env: Mapping[str, str],
    state: gpu.GpuState | None,
    *,
    force: bool,
    idle_mib: int = gpu.IDLE_MIB,
) -> str | None:
    """Why this run must not start, or None. The CPU path is refused even under `--force`."""
    if gpu.cuda_hidden(env):
        return (
            f"CUDA is hidden (CUDA_VISIBLE_DEVICES={env.get('CUDA_VISIBLE_DEVICES')!r}); "
            "gpu-bench measures the GPU, so it does not run on the CPU path"
        )
    if state is None:
        return "no nvidia-smi: there is no CUDA device to measure"
    if not force:
        return gpu.busy_reason(state, idle_mib=idle_mib)
    return None


def measure(metrics: Mapping[str, Any], *, effective_batch: int) -> dict[str, Any]:
    """s/step over the timed steps, s/epoch on this fold's training rows, peak GiB."""
    steps = list(metrics.get("step_seconds") or [])
    if len(steps) < WARMUP + TIMED:
        raise BenchError(f"{len(steps)} steps recorded; need {WARMUP} warm-up + {TIMED} timed")
    peak = metrics.get("peak_cuda_mem_bytes")
    if peak is None:
        raise BenchError("the run recorded no CUDA peak memory: it did not train on CUDA")
    timed = steps[WARMUP : WARMUP + TIMED]
    per_step = float(np.mean(timed))
    per_epoch_steps = math.ceil(int(metrics["train_rows"]) / effective_batch)
    return {
        "seconds_per_step": per_step,
        "seconds_per_step_sd": float(np.std(timed)),
        "steps_per_epoch": per_epoch_steps,
        "seconds_per_epoch": per_step * per_epoch_steps,
        "peak_gib": float(peak) / 1024**3,
        "train_rows": int(metrics["train_rows"]),
    }


def choose(measured: Mapping[str, Mapping[str, Any]]) -> dict[str, str | None]:
    """§6.4: per architecture, the fastest variant within the memory limit (None if none fits)."""
    out: dict[str, str | None] = {}
    for arch, profiles in CANDIDATES.items():
        fits = [p for p in profiles if p in measured and measured[p]["peak_gib"] <= PEAK_LIMIT_GIB]
        out[arch] = min(fits, key=lambda p: measured[p]["seconds_per_epoch"]) if fits else None
    return out


def nondeterminism(a: Path, b: Path) -> dict[str, Any]:
    """Two runs of one (profile, fold, seed): how far apart their OOF predictions landed."""
    from sieve.eval.metrics import macro_f1
    from sieve.types import LABELS

    logits_a, logits_b = (np.load(d / "oof_logits.npy") for d in (a, b))
    labels = np.load(a / "oof_labels.npy")
    truth = [LABELS[int(i)] for i in labels]
    f1 = [macro_f1(truth, [LABELS[int(i)] for i in x.argmax(axis=1)]) for x in (logits_a, logits_b)]
    return {
        "macro_f1_a": f1[0],
        "macro_f1_b": f1[1],
        "delta_macro_f1": abs(f1[0] - f1[1]),
        "max_abs_logit_diff": float(np.max(np.abs(logits_a - logits_b))),
        "identical_logits": bool(np.array_equal(logits_a, logits_b)),
    }


def _effective_batch(config: Path, profile: str) -> int:
    import yaml

    fields = yaml.safe_load(config.read_text(encoding="utf-8"))["profiles"][profile]
    return int(fields["batch_size"]) * int(fields.get("gradient_accumulation_steps", 1))


def main(argv: Sequence[str] | None = None, *, runner: Runner = run_module) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="python -m bench.gpu_bench")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("bench/results/gpu_bench.json"))
    parser.add_argument("--work", type=Path, default=Path("models/_gpu_bench"))
    parser.add_argument("--force", action="store_true", help="measure even if the card is busy")
    parser.add_argument("--idle-mib", type=int, default=gpu.IDLE_MIB)
    ns = parser.parse_args(argv)

    def gate() -> gpu.GpuState | None:
        state = gpu.query()
        reason = refusal(os.environ, state, force=ns.force, idle_mib=ns.idle_mib)
        if reason:
            print(f"gpu-bench refused: {reason}", file=sys.stderr)
            raise SystemExit(2)
        return state

    start_state = gate()
    corpus = ns.corpus
    if corpus is None:
        from sieve.model.train import default_corpus

        corpus = default_corpus(ns.config)

    def train(profile: str, out: Path, *extra: str) -> dict[str, Any]:
        gate()
        runner(["sieve.model.train", "--config", str(ns.config), "--profile", profile,
                "--fold", "0", "--seed", "0", "--corpus", str(corpus), "--out", str(out),
                *extra])  # fmt: skip
        return dict(json.loads((out / "metrics.json").read_text(encoding="utf-8")))

    variants: dict[str, dict[str, Any]] = {}
    for profile in VARIANTS_IN_ORDER:
        metrics = train(profile, ns.work / profile, "--max-steps", str(WARMUP + TIMED))
        measured = measure(metrics, effective_batch=_effective_batch(ns.config, profile))
        role = "ablation" if profile in ABLATION_VARIANTS else "candidate"
        variants[profile] = {**measured, "family": family(ns.config, profile), "role": role}

    choice = choose(variants)
    repeats: dict[str, Any] = {}
    for arch, chosen in choice.items():
        if chosen is None:
            continue
        runs = [ns.work / f"{chosen}-repeat-{k}" for k in ("a", "b")]
        for run_dir in runs:
            train(chosen, run_dir, "--epochs", str(REPEAT_EPOCHS))
        repeats[arch] = {"profile": chosen, **nondeterminism(*runs)}

    result = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git_sha(short=False),
        "warmup_steps": WARMUP,
        "timed_steps": TIMED,
        "peak_limit_gib": PEAK_LIMIT_GIB,
        "rule": "per architecture, the lowest s/epoch whose peak max_memory_allocated <= 11.0 GiB",
        "gpu_at_start": None
        if start_state is None
        else {
            "memory_used_mib": start_state.memory_used_mib,
            "memory_total_mib": start_state.memory_total_mib,
        },
        "variants": variants,
        "choice": choice,
        "nondeterminism": repeats,
    }
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")

    for arch, chosen in choice.items():
        if chosen is None:
            print(f"{arch}: no variant fits {PEAK_LIMIT_GIB} GiB -- stop and ask the owner")
        else:
            v = variants[chosen]
            print(f"{arch}: {chosen} ({v['seconds_per_epoch']:.1f} s/epoch, "
                  f"peak {v['peak_gib']:.2f} GiB) -> make sweep PROFILE={chosen}")  # fmt: skip
    for arch, r in repeats.items():
        print(f"{arch}: non-determinism, delta macro-F1 {r['delta_macro_f1']:.4f}, "
              f"max |logit diff| {r['max_abs_logit_diff']:.2e}")  # fmt: skip
    return result


if __name__ == "__main__":  # pragma: no cover
    main()
