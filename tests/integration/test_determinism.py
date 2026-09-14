"""Training the same (profile, fold, seed) twice produces the same artefact.

Every published number is a function of a training run (PRD M6a), so "re-run it and you get the
same thing" is the property that makes `make bench` a reproduction rather than a re-measurement.
This is the cheapest honest test of it: the `tiny` CI smoke profile, seed 0, fold 0, twice, into
two temporary directories that share nothing.

**Two subprocesses, not two calls.** Running both trainings in the pytest interpreter would let
the second inherit the first's state -- a seeded global RNG, a warm `transformers` cache, an
import-time singleton -- and any of those could make two runs agree for a reason the runbook's
`make train` does not have. Each run is `python -m sieve.model.train`, the command the Makefile
issues, in its own process with its own fresh interpreter state.

Two artefacts are compared, and they answer different questions:

* `oof_logits.npy` -- **byte-identical**. These are the pooled out-of-fold predictions every
  downstream decision is made from (thresholds, calibration, ablations, error analysis). A
  difference here, however small, is a different set of verdicts.
* `metrics.json` -- identical **except `wall_clock_seconds`**, which is a measurement of the
  machine and not an output of the training. Comparing the two files byte-for-byte would assert
  that two runs took the same number of milliseconds, which is not determinism and is not true.

The cross-seed control is part of the test: seed 1 into a third directory, asserted to differ
from seed 0. Without it, a comparison that accidentally compared nothing would also pass.

Marked `slow`, like the rest of the training tests: it runs in CI's `model` job, never on every
push. It skips rather than fails when the base weights cannot be obtained, so a box with no
network and a cold Hugging Face cache reports "not measured" instead of a red training run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="the model extra is not installed")
pytest.importorskip("transformers", reason="the model extra is not installed")

from sieve.types import CorpusRecord  # noqa: E402
from tests.model.conftest import assigned_mini_50, close_hub_session  # noqa: E402

#: `bench/train.yaml`'s `tiny` profile, and the one repo `tests/model/hub_fixtures.txt` records.
TINY = "prajjwal1/bert-tiny"

#: Written by a `--fold 0` run; every one of them has to match, not just the logits.
ARTEFACTS = ("oof_logits.npy", "oof_labels.npy", "early_stop_logits.npy", "oof_ids.json")


@pytest.fixture(scope="module", autouse=True)
def _close_the_hub_session() -> Iterator[None]:
    """`huggingface_hub` pools a TLS connection; left open it is a `ResourceWarning` later."""
    yield
    close_hub_session()


@pytest.fixture(scope="module")
def base_weights() -> None:
    """Skip the module when the base model cannot be obtained, rather than failing on it."""
    from transformers import AutoConfig

    try:
        AutoConfig.from_pretrained(TINY)
    except OSError as exc:  # offline, and nothing in the cache
        pytest.skip(f"{TINY} is neither cached nor reachable: {exc}")


def _corpus(path: Path) -> Path:
    records: list[CorpusRecord] = [r for r in assigned_mini_50() if r.split == "dev"]
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _train(corpus: Path, out: Path, *, seed: str = "0") -> Path:
    """One `python -m sieve.model.train` run, in its own interpreter. Raises on a bad exit."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    argv = [
        sys.executable, "-m", "sieve.model.train",
        "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "0",
        "--seed", seed, "--corpus", str(corpus), "--out", str(out),
    ]  # fmt: skip
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"training exited {result.returncode}\n--- stdout\n{result.stdout}"
            f"\n--- stderr\n{result.stderr}"
        )
    return out


def _metrics_without_the_clock(directory: Path) -> dict:
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics.pop("wall_clock_seconds") > 0, "the run has to have taken some time"
    return metrics


@pytest.mark.slow
def test_the_same_seed_trains_the_same_artefact_twice(base_weights: None, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus.jsonl")
    first = _train(corpus, tmp_path / "run-a")
    second = _train(corpus, tmp_path / "run-b")

    assert _metrics_without_the_clock(first) == _metrics_without_the_clock(second)
    for name in ARTEFACTS:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


@pytest.mark.slow
def test_a_different_seed_does_not_produce_the_same_artefact(
    base_weights: None, tmp_path: Path
) -> None:
    """The control for the test above: the comparison it makes is a real constraint.

    If seed 0 and seed 1 also matched, the equality asserted above would be telling us something
    about the comparison rather than about the training.
    """
    corpus = _corpus(tmp_path / "corpus.jsonl")
    zero = _train(corpus, tmp_path / "seed-0", seed="0")
    one = _train(corpus, tmp_path / "seed-1", seed="1")

    assert _metrics_without_the_clock(zero) != _metrics_without_the_clock(one)
    assert (zero / "oof_logits.npy").read_bytes() != (one / "oof_logits.npy").read_bytes()
