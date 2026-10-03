"""The Trainer settings `sieve.model.train` builds (training plan B1, B2, B5)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sieve.model.train import collator, load_profile, training_arguments

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

os.environ.setdefault("HF_HUB_OFFLINE", "1")
TINY = "prajjwal1/bert-tiny"
CONFIG = Path("bench/train.yaml")


def _tiny_trainer(tmp_path: Path, lengths: list[int], **overrides: Any) -> Any:
    from datasets import Dataset  # type: ignore[import-untyped]
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer

    tokenizer = AutoTokenizer.from_pretrained(TINY)
    model = AutoModelForSequenceClassification.from_pretrained(TINY, num_labels=3)
    rng = np.random.default_rng(0)
    rows = [
        {
            "input_ids": [101] + [int(x) for x in rng.integers(1000, 2000, n - 2)] + [102],
            "labels": 0,
        }
        for n in lengths
    ]
    data = Dataset.from_list(rows).map(lambda r: {"attention_mask": [1] * len(r["input_ids"])})
    config = load_profile(CONFIG, "tiny", fold=0, seed=0)
    args = training_arguments(config, tmp_path / "ckpt", has_early=False, **overrides)
    return Trainer(
        model=model,
        args=args,
        train_dataset=data,
        processing_class=tokenizer,
        data_collator=collator(tokenizer),
    )


def _padded_tokens(trainer: Any) -> int:
    return sum(int(batch["input_ids"].numel()) for batch in trainer.get_train_dataloader())


def test_training_uses_the_length_grouped_sampler(tmp_path: Path) -> None:
    from transformers.trainer_pt_utils import LengthGroupedSampler

    trainer = _tiny_trainer(tmp_path, [20, 30, 40] * 20)
    assert trainer.args.group_by_length
    assert isinstance(trainer._get_train_sampler(), LengthGroupedSampler)


def test_length_grouping_pads_far_less_than_random_batches(tmp_path: Path) -> None:
    lengths = [int(x) for x in np.random.default_rng(1).integers(8, 128, size=960)]
    grouped = _padded_tokens(_tiny_trainer(tmp_path, lengths))
    shuffled = _padded_tokens(_tiny_trainer(tmp_path, lengths, group_by_length=False))
    assert grouped <= 0.6 * shuffled


def test_checkpoints_keep_no_optimizer_state(tmp_path: Path) -> None:
    config = load_profile(CONFIG, "tiny", fold=0, seed=0)
    args = training_arguments(config, tmp_path / "ckpt", has_early=True)
    assert args.save_only_model and args.load_best_model_at_end and args.save_total_limit == 1


@pytest.mark.slow
def test_a_run_writes_no_optimizer_state_and_at_most_two_checkpoints(
    dev_records: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from sieve.model import train

    kept: list[Path] = []
    monkeypatch.setattr(train.shutil, "rmtree", lambda p, **_: kept.append(Path(p)))
    corpus = tmp_path / "c.jsonl"
    corpus.write_text("".join(r.model_dump_json() + "\n" for r in dev_records), encoding="utf-8")
    out = tmp_path / "run"
    train.main(["--profile", "tiny", "--fold", "0", "--corpus", str(corpus), "--out", str(out)])
    checkpoints = [p for p in (out / "_checkpoints").iterdir() if p.is_dir()]
    assert 1 <= len(checkpoints) <= 2
    assert not list((out / "_checkpoints").rglob("optimizer.pt"))
    assert not list((out / "_checkpoints").rglob("scheduler.pt"))
    shutil.rmtree(out / "_checkpoints")
