"""Training the same (profile, fold, seed) twice produces the same artefact.

Every published number is a function of a training run (PRD M6a), so "re-run it and you get the
same thing" is the property that makes `make bench` a reproduction rather than a re-measurement.
This is the cheapest honest test of it: the `tiny` CI smoke profile, seed 0, fold 0, twice, into
two temporary directories that share nothing.

Two artefacts are compared, and they answer different questions:

* `oof_logits.npy` — **byte-identical**. These are the pooled out-of-fold predictions every
  downstream decision is made from (thresholds, calibration, ablations, error analysis). A
  difference here, however small, is a different set of verdicts.
* `metrics.json` — identical **except `wall_clock_seconds`**, which is a measurement of the
  machine and not an output of the training. Comparing the two files byte-for-byte would assert
  that two runs took the same number of milliseconds, which is not determinism and is not true.

Marked `slow`, like the rest of the training tests: it runs in CI's `model` job, never on every
push. It skips rather than fails when the base weights cannot be obtained, so a box with no
network and a cold Hugging Face cache reports "not measured" instead of a red training run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="the model extra is not installed")
pytest.importorskip("transformers", reason="the model extra is not installed")

from sieve.model.train import main  # noqa: E402
from sieve.types import CorpusRecord  # noqa: E402
from tests.model.conftest import assigned_mini_50, close_hub_session  # noqa: E402

# Targeted, as tests/model does it: the project turns warnings into errors on purpose, and only
# the noise `transformers`/`torch` emit on a CPU training run is filtered here.
pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

#: `bench/train.yaml`'s `tiny` profile. Named here only to report it in a skip reason.
TINY = "prajjwal1/bert-tiny"


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


def _train(corpus: Path, out: Path) -> Path:
    main(
        [
            "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "0",
            "--seed", "0", "--corpus", str(corpus), "--out", str(out),
        ]
    )  # fmt: skip
    return out


@pytest.mark.slow
def test_the_same_seed_trains_the_same_artefact_twice(base_weights: None, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus.jsonl")
    first = _train(corpus, tmp_path / "run-a")
    second = _train(corpus, tmp_path / "run-b")

    a = json.loads((first / "metrics.json").read_text(encoding="utf-8"))
    b = json.loads((second / "metrics.json").read_text(encoding="utf-8"))
    assert a.pop("wall_clock_seconds") > 0
    assert b.pop("wall_clock_seconds") > 0
    assert a == b

    for name in ("oof_logits.npy", "oof_labels.npy", "early_stop_logits.npy"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert (first / "oof_ids.json").read_bytes() == (second / "oof_ids.json").read_bytes()
