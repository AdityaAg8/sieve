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
