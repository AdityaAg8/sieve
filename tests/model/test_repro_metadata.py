"""Reproducibility metadata (training plan A5): pinned revisions, clean trees, provenance fields."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sieve.model import version
from sieve.model.train import file_sha256, library_versions, load_profile, main
from sieve.types import CorpusRecord

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

CONFIG = Path("bench/train.yaml")
DEBERTA_MAIN = "8ccc9b6f36199bec6961081d44eb72fb3f7353f3"
MODERNBERT = "8949b909ec900327062f0ebf497f51aef5e6f0c8"


@pytest.mark.parametrize(
    ("profile", "revision"),
    [("deberta", DEBERTA_MAIN), ("modernbert", MODERNBERT), ("modernbert-1024", MODERNBERT)],
)
def test_headline_profiles_pin_their_base_model_revision(profile: str, revision: str) -> None:
    assert load_profile(CONFIG, profile, fold=0, seed=0).revision == revision


def test_a_dirty_tree_is_refused_for_a_real_profile() -> None:
    with pytest.raises(version.DirtyTreeRefused, match="sieve/model/train.py"):
        version.require_clean_tree(
            "deberta", allow_dirty=False, status=lambda: [" M sieve/model/train.py"]
        )


def test_allow_dirty_lets_a_dirty_tree_through() -> None:
    version.require_clean_tree("deberta", allow_dirty=True, status=lambda: [" M sieve/x.py"])


def test_the_tiny_smoke_profile_never_needs_a_clean_tree() -> None:
    version.require_clean_tree("tiny", allow_dirty=False, status=lambda: [" M sieve/x.py"])


def test_a_clean_tree_passes() -> None:
    version.require_clean_tree("deberta", allow_dirty=False, status=lambda: [])


def test_the_dirty_check_ignores_results_and_data() -> None:
    spec = version.DIRTY_PATHSPEC
    assert {"sieve", "bench", "pyproject.toml", "uv.lock", "Makefile"} <= set(spec)
    assert ":(exclude)bench/results" in spec and ":(exclude)bench/data" in spec


def test_load_profile_carries_allow_dirty() -> None:
    assert load_profile(CONFIG, "deberta", fold=0, seed=0, allow_dirty=True).allow_dirty


def test_file_sha256_is_the_hash_of_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_bytes(b"abc\n")
    assert file_sha256(path) == hashlib.sha256(b"abc\n").hexdigest()


def test_library_versions_name_every_training_library() -> None:
    got = library_versions()
    assert {
        "python",
        "torch",
        "transformers",
        "accelerate",
        "datasets",
        "numpy",
        "tokenizers",
    } <= set(got)
    assert all(isinstance(v, str) and v for v in got.values())


@pytest.mark.slow
def test_metrics_record_the_provenance_fields(
    dev_records: list[CorpusRecord], tmp_path: Path
) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev_records), encoding="utf-8", newline="\n"
    )
    out = tmp_path / "run"
    main(
        [
            "--profile",
            "tiny",
            "--fold",
            "0",
            "--seed",
            "0",
            "--corpus",
            str(corpus),
            "--out",
            str(out),
        ]
    )
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["corpus_sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert metrics["git_sha"] == version.git_sha(short=False)
    assert isinstance(metrics["git_dirty"], bool)
    assert set(metrics["library_versions"]) >= {"torch", "transformers"}
    assert isinstance(metrics["best_global_step"], int) and metrics["best_global_step"] > 0
    assert metrics["profile_variant"] == "bs16"
    assert metrics["cudnn_deterministic"] is True


@pytest.mark.slow
def test_metrics_record_throughput_and_loss(
    dev_records: list[CorpusRecord], tmp_path: Path
) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(r.model_dump_json() + "\n" for r in dev_records), encoding="utf-8")
    out = tmp_path / "run"
    main(["--profile", "tiny", "--fold", "1", "--corpus", str(corpus), "--out", str(out)])
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert m["corpus_sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert isinstance(m["eval_loss_per_epoch"], list) and m["eval_loss_per_epoch"]
    assert all(isinstance(x, float) for x in m["eval_loss_per_epoch"])
    assert len(m["eval_loss_per_epoch"]) == len(m["early_stop_macro_f1_per_epoch"])
    for key in ("train_seconds", "seconds_per_step", "tokens_per_second"):
        assert isinstance(m[key], float) and m[key] > 0, key
    assert m["peak_cuda_mem_bytes"] is None  # CPU run
