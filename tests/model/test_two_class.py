"""The two-class head end to end through thresholds and calibration (training plan B11, F47).

Ablation row 12 trains `num_labels=2` (`not_poisoned`, `poisoned`). Its OOF logits have two
columns and its OOF labels are head ids 0/1, so reading column 2 or decoding the labels with the
three-class names crashed calibration or silently found no poisoned rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sieve.model import thresholds as th
from sieve.model.calibrate import PoolError, main, pool_oof
from sieve.model.data import LABEL2ID
from sieve.types import LABELS


def test_the_label_map_is_explicit_and_poisoned_is_the_last_column() -> None:
    assert th.head_labels(3) == LABELS
    assert th.head_labels(2) == ("not_poisoned", "poisoned")
    for n in (2, 3):
        assert th.head_labels(n)[-1] == "poisoned"
    with pytest.raises(ValueError, match="columns"):
        th.head_labels(4)


def test_the_training_head_uses_the_same_label_map() -> None:
    from sieve.model.train import TWO_CLASS_ID2LABEL

    assert tuple(TWO_CLASS_ID2LABEL[i] for i in sorted(TWO_CLASS_ID2LABEL)) == th.head_labels(2)


def test_two_columns_select_the_same_operating_point_as_the_widened_block() -> None:
    rng = np.random.default_rng(3)
    three = rng.integers(0, 3, size=400)
    poisoned = np.clip(rng.normal(0.25 + 0.6 * (three == 2), 0.15), 0.0, 1.0)
    two = np.stack([1.0 - poisoned, poisoned], axis=1)
    widened = np.zeros((400, 3))
    widened[:, 0], widened[:, 2] = two[:, 0], two[:, 1]
    groups = [f"s{i % 120}" for i in range(400)]

    got = th.select(two, (three == 2).astype(int), groups, temperature=1.0, n_boot=60)
    want = th.select(widened, three, groups, temperature=1.0, n_boot=60)
    assert got == want
    assert got.val_recall_at_t_low > 0.0


def _two_class_fold(directory: Path, records: list[Any], fold: int, rng: Any) -> Path:
    held = [r for r in records if r.fold == fold]
    directory.mkdir(parents=True, exist_ok=True)
    labels = np.asarray([int(LABEL2ID[r.label] == LABEL2ID["poisoned"]) for r in held])
    logits = rng.normal(size=(len(held), 2))
    logits[np.arange(len(held)), labels] += 2.5
    np.save(directory / "oof_logits.npy", logits.astype(np.float32))
    np.save(directory / "oof_labels.npy", labels.astype(np.int64))
    (directory / "oof_ids.json").write_text(
        json.dumps([r.id for r in held]), encoding="utf-8", newline="\n"
    )
    config = {"model_version": "deberta-2c@abc1234", "fold": fold, "seed": 0, "mode": "full",
              "num_labels": 2, "temperature": None, "t_low": None, "t_high": None,
              "training_command": f"python -m sieve.model.train --fold {fold}"}  # fmt: skip
    (directory / "sieve_config.json").write_text(json.dumps(config), encoding="utf-8", newline="\n")
    return directory


def _corpus(records: list[Any], path: Path) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _args(dirs: list[Path], corpus: Path, results: Path) -> list[str]:
    return ["--dirs", *[str(d) for d in dirs], "--corpus", str(corpus),
            "--results-dir", str(results), "--n-boot", "40", "--curve-boot", "20",
            "--curve-points", "10"]  # fmt: skip


def test_calibrate_fills_a_two_class_artefact(dev_records: list[Any], tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    seed_dir = tmp_path / "models" / "deberta-2c-s0"
    dirs = [_two_class_fold(seed_dir / f"fold{k}", dev_records, k, rng) for k in range(5)]
    written = main(_args(dirs, _corpus(dev_records, tmp_path / "c.jsonl"), tmp_path / "r"))

    loaded = th.load(written.parent)
    assert 0.0 <= loaded.t_low <= loaded.t_high <= 1.0
    assert loaded.val_recall_at_t_low > 0.0  # poisoned rows were found in column 1
    assert np.load(written.parent / "oof_logits.npy").shape[1] == 2


def test_logits_that_contradict_the_configured_head_are_refused(
    dev_records: list[Any], tmp_path: Path
) -> None:
    rng = np.random.default_rng(5)
    dirs = [_two_class_fold(tmp_path / f"fold{k}", dev_records, k, rng) for k in range(5)]
    np.save(
        dirs[2] / "oof_logits.npy", rng.normal(size=(len(np.load(dirs[2] / "oof_labels.npy")), 3))
    )
    with pytest.raises(PoolError, match="num_labels"):
        pool_oof(dirs)


def test_labels_outside_the_head_are_refused(dev_records: list[Any], tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    dirs = [_two_class_fold(tmp_path / f"fold{k}", dev_records, k, rng) for k in range(5)]
    labels = np.load(dirs[0] / "oof_labels.npy")
    labels[0] = 2
    np.save(dirs[0] / "oof_labels.npy", labels)
    with pytest.raises(PoolError, match="label"):
        pool_oof(dirs)


@pytest.mark.slow
@pytest.mark.filterwarnings("ignore::FutureWarning")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_calibrate_runs_on_a_trained_two_class_tiny_artefact(tmp_path: Path) -> None:
    from sieve.model.data import load_corpus
    from sieve.model.train import main as train_main

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    dev = [r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}) for r in records]
    corpus = _corpus(dev, tmp_path / "corpus.jsonl")
    seed_dir = tmp_path / "models" / "tiny-s0"
    dirs = []
    for fold in range(5):
        out = seed_dir / f"fold{fold}"
        train_main(["--config", "bench/train.yaml", "--profile", "tiny", "--fold", str(fold),
                    "--seed", "0", "--num-labels", "2", "--allow-dirty",
                    "--corpus", str(corpus), "--out", str(out)])  # fmt: skip
        assert np.load(out / "oof_logits.npy").shape[1] == 2
        dirs.append(out)

    written = main(_args(dirs, corpus, tmp_path / "results"))
    shared = json.loads(written.read_text(encoding="utf-8"))
    assert shared["num_labels"] == 2
    loaded = th.load(written.parent)
    # A two-epoch model on 50 rows never reaches 0.95 precision, so `t_high` may sit just past
    # 1.0 (nothing auto-confirms); the point here is that calibration runs on two columns.
    assert 0.0 <= loaded.t_low <= loaded.t_high
    assert 0.05 <= loaded.temperature <= 10.0
