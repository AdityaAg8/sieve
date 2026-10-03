"""One loss for both architectures (training plan B7, finding F15): Σwℓ/Σw, always."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import torch

from sieve.model.train import load_profile, training_arguments
from sieve.model.weighted_trainer import WeightedTrainer

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

WEIGHTS = [0.5, 1.0, 3.0]


def _bert() -> Any:
    from transformers import BertConfig, BertForSequenceClassification

    config = BertConfig(
        vocab_size=120, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=32, hidden_dropout_prob=0.0, attention_probs_dropout_prob=0.0,
        num_labels=3,
    )  # fmt: skip
    return BertForSequenceClassification(config)


def _modernbert() -> Any:
    from transformers import ModernBertConfig, ModernBertForSequenceClassification

    config = ModernBertConfig(
        vocab_size=120, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=32, attention_dropout=0.0, mlp_dropout=0.0, embedding_dropout=0.0,
        classifier_dropout=0.0, num_labels=3, pad_token_id=0,
    )  # fmt: skip
    config._attn_implementation = "eager"
    return ModernBertForSequenceClassification(config)


def _batches(n_batches: int, size: int = 4, seq: int = 7) -> list[dict[str, torch.Tensor]]:
    gen = torch.Generator().manual_seed(0)
    out = []
    for _ in range(n_batches):
        out.append(
            {
                "input_ids": torch.randint(5, 120, (size, seq), generator=gen),
                "attention_mask": torch.ones(size, seq, dtype=torch.long),
                "labels": torch.randint(0, 3, (size,), generator=gen),
            }
        )
    return out


@pytest.mark.parametrize("build", [_bert, _modernbert], ids=["bert", "modernbert"])
@pytest.mark.parametrize("accumulation", [1, 2, 3])
def test_the_window_loss_is_the_weighted_mean(
    build: Any, accumulation: int, tmp_path: Path
) -> None:
    torch.manual_seed(0)
    model = build()
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0)
    args = training_arguments(
        config, tmp_path, has_early=False, gradient_accumulation_steps=accumulation
    )
    trainer = WeightedTrainer(model=model, args=args, class_weights=WEIGHTS, num_labels=3)
    assert trainer.model_accepts_loss_kwargs is False

    batches = _batches(accumulation)
    assert trainer._get_num_items_in_batch(batches, torch.device("cpu")) is None
    trainer.current_gradient_accumulation_steps = len(batches)
    model.train()
    # What `training_step` backpropagates: compute_loss / accumulation steps, per micro-batch.
    total = sum(
        trainer.compute_loss(model, dict(b)) / trainer.current_gradient_accumulation_steps
        for b in batches
    )

    logits = torch.cat(
        [
            model(input_ids=b["input_ids"], attention_mask=b["attention_mask"]).logits
            for b in batches
        ]
    )
    labels = torch.cat([b["labels"] for b in batches])
    expected = torch.nn.CrossEntropyLoss(weight=torch.tensor(WEIGHTS), reduction="mean")(
        logits, labels
    )
    assert float(total) == pytest.approx(float(expected), abs=1e-6)


def test_evaluation_uses_the_plain_weighted_mean(tmp_path: Path) -> None:
    torch.manual_seed(0)
    model = _bert().eval()
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0)
    trainer = WeightedTrainer(
        model=model,
        args=training_arguments(config, tmp_path, has_early=False),
        class_weights=WEIGHTS,
        num_labels=3,
    )
    batch = _batches(1)[0]
    got = trainer.compute_loss(model, dict(batch))
    logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits
    expected = torch.nn.CrossEntropyLoss(weight=torch.tensor(WEIGHTS))(logits, batch["labels"])
    assert float(got) == pytest.approx(float(expected), abs=1e-6)


def test_without_class_weights_it_is_the_plain_mean(tmp_path: Path) -> None:
    torch.manual_seed(0)
    model = _bert()
    config = load_profile(Path("bench/train.yaml"), "tiny", fold=0, seed=0)
    trainer = WeightedTrainer(
        model=model,
        args=training_arguments(config, tmp_path, has_early=False),
        class_weights=None,
        num_labels=3,
    )
    batches = _batches(2)
    trainer._get_num_items_in_batch(batches, torch.device("cpu"))
    trainer.current_gradient_accumulation_steps = 2
    model.train()
    total = sum(trainer.compute_loss(model, dict(b)) / 2 for b in batches)
    logits = torch.cat(
        [
            model(input_ids=b["input_ids"], attention_mask=b["attention_mask"]).logits
            for b in batches
        ]
    )
    expected = torch.nn.CrossEntropyLoss()(logits, torch.cat([b["labels"] for b in batches]))
    assert float(total) == pytest.approx(float(expected), abs=1e-6)
