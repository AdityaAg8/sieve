from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pytest
from transformers import AutoTokenizer

from sieve.model.data import build_splits, load_corpus
from sieve.model.train import (
    PrecisionRefused,
    TrainConfig,
    load_profile,
    main,
    payload_survival_failures,
)
from sieve.types import CorpusRecord

# Targeted, not blanket: the project turns warnings into errors on purpose. Only the
# noise `transformers`/`torch` emit on a CPU training or export run is filtered, so a
# new deprecation in this lane's own code still fails the run.
pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

TINY = "prajjwal1/bert-tiny"


def _write_corpus(path: Path, records: list[CorpusRecord]) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


@pytest.fixture
def corpus_with_a_sealed_row(
    balanced_dev_records: list[CorpusRecord], tmp_path: Path
) -> tuple[Path, list[CorpusRecord]]:
    sealed = balanced_dev_records[0].model_copy(
        update={"id": "sealed-holdout", "server_name": "sealed-server", "split": "holdout"}
    )
    return (
        _write_corpus(tmp_path / "corpus.jsonl", [*balanced_dev_records, sealed]),
        balanced_dev_records,
    )


# --------------------------------------------------------------------------- config


def test_tiny_profile_loads_from_the_committed_yaml() -> None:
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0)
    assert config.base_model == TINY
    assert config.warmup_ratio == 0.06
    assert config.num_labels == 3
    assert config.precision == "fp32"


def test_deberta_profile_is_fp32() -> None:
    config = load_profile(Path("bench/train.yaml"), "deberta", fold=0, seed=0)
    assert "deberta" in config.base_model
    assert config.precision == "fp32"


def test_bf16_on_deberta_is_refused_without_the_explicit_flag() -> None:
    with pytest.raises(PrecisionRefused, match="FP32"):
        TrainConfig(
            base_model="microsoft/deberta-v3-base",
            max_length=512,
            lr=2e-5,
            batch_size=16,
            epochs=8,
            patience=2,
            precision="bf16",
            seed=0,
            fold=0,
            class_weights=True,
        )


def test_bf16_on_deberta_is_allowed_when_the_ablation_says_so() -> None:
    config = TrainConfig(
        base_model="microsoft/deberta-v3-base",
        max_length=512,
        lr=2e-5,
        batch_size=16,
        epochs=8,
        patience=2,
        precision="bf16",
        seed=0,
        fold=0,
        class_weights=True,
        allow_reduced_precision=True,
    )
    assert config.precision == "bf16"


def test_fold_all_requires_an_explicit_epoch_count() -> None:
    with pytest.raises(ValueError, match="--epochs"):
        TrainConfig(
            base_model=TINY,
            max_length=128,
            lr=5e-4,
            batch_size=16,
            epochs=2,
            patience=1,
            precision="fp32",
            seed=0,
            fold="all",
            class_weights=True,
        )


def test_fold_all_is_accepted_with_an_explicit_epoch_count() -> None:
    config = TrainConfig(
        base_model=TINY,
        max_length=128,
        lr=5e-4,
        batch_size=16,
        epochs=2,
        patience=1,
        precision="fp32",
        seed=0,
        fold="all",
        class_weights=True,
        epochs_explicit=True,
    )
    assert config.fold == "all"


def test_two_class_head_collapses_the_label_map() -> None:
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0, num_labels=2)
    assert config.label_map() == {0: 0, 1: 0, 2: 1}
    assert config.id2label() == {0: "not_poisoned", 1: "poisoned"}


def test_three_class_label_map_is_the_identity() -> None:
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0)
    assert config.label_map() == {0: 0, 1: 1, 2: 2}


# ----------------------------------------------------------------- payload survival


def test_every_payload_field_survives_tokenisation_at_the_configured_length(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    """PRD §22: truncation must remove context, never evidence."""
    tokenizer = AutoTokenizer.from_pretrained(TINY)
    failures = payload_survival_failures(balanced_dev_records, tokenizer, max_length=512)
    assert failures == [], f"payload truncated: {failures}"


def test_payload_survival_reports_the_record_and_the_field(
    balanced_dev_records: list[CorpusRecord],
) -> None:
    tokenizer = AutoTokenizer.from_pretrained(TINY)
    poisoned = next(r for r in balanced_dev_records if r.label == "poisoned" and r.payload_fields)
    failures = payload_survival_failures([poisoned], tokenizer, max_length=8)
    assert failures == [(poisoned.id, poisoned.payload_fields[0])]


# ------------------------------------------------------------------------ smoke run


@pytest.mark.slow
def test_tiny_profile_fold_zero_writes_every_artefact_and_no_sealed_file(
    corpus_with_a_sealed_row: tuple[Path, list[CorpusRecord]],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    corpus, dev = corpus_with_a_sealed_row
    out = tmp_path / "tiny-f0-s0"
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="sieve.model"):
        main(
            [
                "--config",
                "bench/train.yaml",
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
    elapsed = time.monotonic() - started
    assert elapsed < 90, f"tiny smoke run took {elapsed:.1f}s"

    for name in (
        "metrics.json",
        "oof_logits.npy",
        "oof_labels.npy",
        "oof_ids.json",
        "early_stop_logits.npy",
        "early_stop_labels.npy",
        "sieve_config.json",
        "config.json",
    ):
        assert (out / name).exists(), name

    written = [p.name.lower() for p in out.rglob("*")]
    assert not [n for n in written if "test" in n or "holdout" in n], written

    expected = sorted(build_splits(dev, fold=0)["oof"]["id"])
    assert json.loads((out / "oof_ids.json").read_text(encoding="utf-8")) == expected

    logits = np.load(out / "oof_logits.npy")
    labels = np.load(out / "oof_labels.npy")
    assert logits.shape == (len(expected), 3)
    assert labels.shape == (len(expected),)

    config = json.loads((out / "sieve_config.json").read_text(encoding="utf-8"))
    assert config["fold"] == 0 and config["seed"] == 0 and config["num_labels"] == 3
    assert config["temperature"] is None and config["t_low"] is None and config["t_high"] is None
    assert config["model_version"].startswith("tiny@")

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["warmup_ratio"] == 0.06
    assert metrics["rows_dropped_not_dev"] == 1
    assert isinstance(metrics["early_stop_macro_f1_per_epoch"], list)
    assert metrics["wall_clock_seconds"] > 0

    assert any("dropped 1 row" in r.getMessage() for r in caplog.records)


@pytest.mark.slow
def test_a_sealed_row_does_not_change_the_run(
    balanced_dev_records: list[CorpusRecord], tmp_path: Path
) -> None:
    """Identical artefacts with and without a `split="holdout"` row in the corpus file."""
    clean = _write_corpus(tmp_path / "clean.jsonl", balanced_dev_records)
    sealed_rec = balanced_dev_records[0].model_copy(
        update={"id": "sealed-holdout", "server_name": "sealed-server", "split": "holdout"}
    )
    dirty = _write_corpus(tmp_path / "dirty.jsonl", [*balanced_dev_records, sealed_rec])
    outs = []
    for name, corpus in (("clean", clean), ("dirty", dirty)):
        out = tmp_path / name
        main(
            [
                "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "0",
                "--seed", "0", "--corpus", str(corpus), "--out", str(out),
            ]
        )  # fmt: skip
        outs.append(out)
    a, b = outs
    assert json.loads((a / "oof_ids.json").read_text(encoding="utf-8")) == json.loads(
        (b / "oof_ids.json").read_text(encoding="utf-8")
    )
    assert np.allclose(np.load(a / "oof_logits.npy"), np.load(b / "oof_logits.npy"), atol=1e-4)


@pytest.mark.slow
def test_fold_all_writes_no_oof_logits(
    balanced_dev_records: list[CorpusRecord], tmp_path: Path
) -> None:
    corpus = _write_corpus(tmp_path / "corpus.jsonl", balanced_dev_records)
    out = tmp_path / "tiny-fall-s0"
    main(
        [
            "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "all",
            "--seed", "0", "--epochs", "1", "--corpus", str(corpus), "--out", str(out),
        ]
    )  # fmt: skip
    assert (out / "sieve_config.json").exists()
    assert not (out / "oof_logits.npy").exists()
    assert not (out / "oof_ids.json").exists()
    assert json.loads((out / "sieve_config.json").read_text(encoding="utf-8"))["fold"] == "all"


def test_corpus_loading_never_yields_a_non_dev_row(tmp_path: Path) -> None:
    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    sealed = records[0].model_copy(update={"id": "x", "split": "holdout"})
    path = _write_corpus(tmp_path / "c.jsonl", [*records, sealed])
    assert any(r.split == "holdout" for r in load_corpus(path))
    assert "x" not in build_splits(load_corpus(path), fold=0)["oof"]["id"]
