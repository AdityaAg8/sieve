from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from sieve.eval.metrics import expected_calibration_error
from sieve.model.calibrate import PoolError, apply, fit_temperature, pool_oof


def _calibrated_logits(
    n: int = 3000, seed: int = 0
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int_]]:
    """Logits whose softmax IS the sampling distribution, so the honest temperature is 1."""
    rng = np.random.default_rng(seed)
    logits = rng.normal(0.0, 1.5, size=(n, 3))
    probs = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    labels = np.asarray([rng.choice(3, p=row) for row in probs], dtype=np.int64)
    return logits, labels


def _write_fold(directory: Path, ids: list[str], labels: list[int], *, seed: int = 0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    logits = rng.normal(size=(len(ids), 3))
    np.save(directory / "oof_logits.npy", logits.astype(np.float32))
    np.save(directory / "oof_labels.npy", np.asarray(labels, dtype=np.int64))
    (directory / "oof_ids.json").write_text(json.dumps(ids), encoding="utf-8", newline="\n")
    return directory


def test_temperature_recovers_a_threefold_overconfidence() -> None:
    logits, labels = _calibrated_logits()
    fitted = fit_temperature(logits * 3.0, labels)
    assert 2.7 <= fitted <= 3.3


def test_temperature_of_already_calibrated_logits_is_about_one() -> None:
    logits, labels = _calibrated_logits()
    assert 0.85 <= fit_temperature(logits, labels) <= 1.15


def test_ece_improves_after_scaling() -> None:
    logits, labels = _calibrated_logits()
    over = logits * 3.0
    before = expected_calibration_error(apply(over, 1.0), labels)
    after = expected_calibration_error(apply(over, fit_temperature(over, labels)), labels)
    assert after < before


def test_apply_returns_a_distribution() -> None:
    logits, _ = _calibrated_logits(n=50)
    probs = apply(logits, 2.5)
    assert probs.shape == (50, 3)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-9)


def test_temperature_is_clamped_to_the_search_bounds() -> None:
    logits, labels = _calibrated_logits(n=400)
    fitted = fit_temperature(logits * 1000.0, labels)
    assert 0.05 <= fitted <= 10.0


def test_pool_oof_concatenates_in_directory_order(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    b = _write_fold(tmp_path / "f1", ["b1"], [1], seed=1)
    logits, labels, ids = pool_oof([a, b])
    assert ids == ["a1", "a2", "b1"]
    assert logits.shape == (3, 3)
    assert list(labels) == [0, 2, 1]


def test_pool_oof_raises_on_overlapping_fold_ids(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    b = _write_fold(tmp_path / "f1", ["a2"], [1], seed=1)
    with pytest.raises(PoolError, match="appear in more than one fold"):
        pool_oof([a, b])


def test_pool_oof_raises_on_a_missing_dev_record(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    with pytest.raises(PoolError, match="missing"):
        pool_oof([a], expected_ids=["a1", "a2", "a3"])


def test_pool_oof_raises_on_an_unexpected_record(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    with pytest.raises(PoolError, match="not in the dev set"):
        pool_oof([a], expected_ids=["a1"])


def test_pool_oof_accepts_the_exact_dev_set(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    b = _write_fold(tmp_path / "f1", ["b1"], [1], seed=1)
    _, _, ids = pool_oof([a, b], expected_ids=["b1", "a2", "a1"])
    assert sorted(ids) == ["a1", "a2", "b1"]


def test_pool_oof_raises_when_a_fold_artefact_is_missing(tmp_path: Path) -> None:
    empty = tmp_path / "f0"
    empty.mkdir()
    with pytest.raises(PoolError, match="oof_logits.npy"):
        pool_oof([empty])


def test_pool_oof_raises_on_a_length_mismatch(tmp_path: Path) -> None:
    a = _write_fold(tmp_path / "f0", ["a1", "a2"], [0, 2])
    (a / "oof_ids.json").write_text(json.dumps(["a1"]), encoding="utf-8", newline="\n")
    with pytest.raises(PoolError, match="length"):
        pool_oof([a])


def _fold_dir(directory: Path, records: list, fold: int, *, rng: np.random.Generator) -> Path:
    """A fake fold artefact: real ids and labels from the fixture, synthetic logits."""
    from sieve.model.data import LABEL2ID

    held = [r for r in records if r.fold == fold]
    directory.mkdir(parents=True, exist_ok=True)
    logits = rng.normal(size=(len(held), 3))
    for row, record in zip(logits, held, strict=True):
        row[LABEL2ID[record.label]] += 2.5
    np.save(directory / "oof_logits.npy", logits.astype(np.float32))
    np.save(
        directory / "oof_labels.npy",
        np.asarray([LABEL2ID[r.label] for r in held], dtype=np.int64),
    )
    (directory / "oof_ids.json").write_text(
        json.dumps([r.id for r in held]), encoding="utf-8", newline="\n"
    )
    (directory / "sieve_config.json").write_text(
        json.dumps(
            {
                "model_version": "tiny@abc1234",
                "fold": fold,
                "seed": 0,
                "mode": "full",
                "num_labels": 3,
                "temperature": None,
                "t_low": None,
                "t_high": None,
                "training_command": f"python -m sieve.model.train --fold {fold}",
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    return directory


def test_cli_fills_every_fold_config_and_writes_the_curves(
    dev_records: list, tmp_path: Path
) -> None:
    from sieve.model import thresholds as th
    from sieve.model.calibrate import main

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev_records),
        encoding="utf-8",
        newline="\n",
    )
    rng = np.random.default_rng(7)
    dirs = [
        _fold_dir(tmp_path / "models" / f"tiny-f{fold}-s0", dev_records, fold, rng=rng)
        for fold in range(5)
    ]
    results = tmp_path / "results"

    written = main(
        [
            "--dirs", *[str(d) for d in dirs],
            "--corpus", str(corpus),
            "--results-dir", str(results),
            "--n-boot", "80",
            "--curve-boot", "40",
            "--curve-points", "20",
        ]
    )  # fmt: skip

    assert written == tmp_path / "models" / "tiny-s0" / "sieve_config.json"
    shared = json.loads(written.read_text(encoding="utf-8"))
    for field in (
        "temperature", "t_low", "t_high", "t_low_spread", "ece_before", "ece_after",
        "routing_rate", "val_recall_at_t_low", "val_precision_at_t_high",
    ):  # fmt: skip
        assert shared[field] is not None, field
    assert shared["fold"] == "pooled"
    assert len(shared["t_low_spread"]) == 5

    # Every per-fold directory is loadable, so a stray fold artefact cannot serve unset thresholds.
    for directory in dirs:
        assert th.load(directory).t_low == shared["t_low"]

    curves = json.loads((results / "curves.json").read_text(encoding="utf-8"))
    assert len(curves["recall_vs_threshold"]) == 20
    assert len(curves["fpr_vs_recall"]) == 20


def _calibrate_nested(
    dev_records: list, tmp_path: Path, *extra: str
) -> tuple[Path, list[Path], Path]:
    """The Makefile's layout: `models/<profile>-s<seed>/fold<k>`, the served model at the top."""
    from sieve.model.calibrate import main

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev_records),
        encoding="utf-8",
        newline="\n",
    )
    rng = np.random.default_rng(11)
    seed_dir = tmp_path / "models" / "tiny-s0"
    dirs = [_fold_dir(seed_dir / f"fold{fold}", dev_records, fold, rng=rng) for fold in range(5)]
    written = main(
        [
            "--dirs", *[str(d) for d in dirs],
            "--corpus", str(corpus),
            "--results-dir", str(tmp_path / "results"),
            "--n-boot", "40",
            "--curve-boot", "20",
            "--curve-points", "10",
            *extra,
        ]
    )  # fmt: skip
    return seed_dir, dirs, written


def test_cli_writes_into_the_seed_directory_its_folds_are_nested_in(
    dev_records: list, tmp_path: Path
) -> None:
    seed_dir, _, written = _calibrate_nested(dev_records, tmp_path)
    assert written == seed_dir / "sieve_config.json"
    assert not (seed_dir / "tiny-s0").exists()


def test_calibration_adds_the_operating_point_without_rewriting_who_made_each_artefact(
    dev_records: list, tmp_path: Path
) -> None:
    """The served model keeps its own `--fold all` command: `push` prints it on the model card."""
    seed_dir = tmp_path / "models" / "tiny-s0"
    seed_dir.mkdir(parents=True)
    whole_dev = {
        "model_version": "tiny@abc1234",
        "fold": "all",
        "seed": 0,
        "mode": "full",
        "num_labels": 3,
        "temperature": None,
        "t_low": None,
        "t_high": None,
        "training_command": "python -m sieve.model.train --fold all --epochs 4",
    }
    (seed_dir / "sieve_config.json").write_text(
        json.dumps(whole_dev), encoding="utf-8", newline="\n"
    )
    (seed_dir / "model.safetensors").write_bytes(b"")

    _, dirs, written = _calibrate_nested(dev_records, tmp_path, "--out", str(seed_dir))

    served = json.loads(written.read_text(encoding="utf-8"))
    assert served["fold"] == "all"
    assert served["training_command"] == whole_dev["training_command"]
    assert served["t_low"] is not None
    for fold, directory in enumerate(dirs):
        own = json.loads((directory / "sieve_config.json").read_text(encoding="utf-8"))
        assert own["fold"] == fold
        assert own["training_command"] == f"python -m sieve.model.train --fold {fold}"
        assert own["t_low"] == served["t_low"]


def test_calibrating_before_the_whole_dev_model_exists_says_it_will_be_blanked(
    dev_records: list, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # `make train FOLD=all` writes a fresh config with blank thresholds over whatever is there.
    with caplog.at_level(logging.WARNING, logger="sieve.model.calibrate"):
        _calibrate_nested(dev_records, tmp_path)
    assert "no whole-dev model" in caplog.text


def test_cli_also_writes_the_pooled_arrays_for_the_selection_step(
    dev_records: list, tmp_path: Path
) -> None:
    """`sieve.model.select` reads one directory per (architecture, seed), not five folds."""
    from sieve.model.calibrate import main

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev_records),
        encoding="utf-8",
        newline="\n",
    )
    rng = np.random.default_rng(3)
    dirs = [
        _fold_dir(tmp_path / "models" / f"tiny-f{fold}-s0", dev_records, fold, rng=rng)
        for fold in range(5)
    ]
    written = main(
        [
            "--dirs", *[str(d) for d in dirs],
            "--corpus", str(corpus),
            "--results-dir", str(tmp_path / "results"),
            "--n-boot", "40", "--curve-boot", "20", "--curve-points", "10",
        ]
    )  # fmt: skip
    shared = written.parent
    ids = json.loads((shared / "oof_ids.json").read_text(encoding="utf-8"))
    assert sorted(ids) == sorted(r.id for r in dev_records)
    assert np.load(shared / "oof_logits.npy").shape == (len(ids), 3)
    assert np.load(shared / "oof_labels.npy").shape == (len(ids),)
