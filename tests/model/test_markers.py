"""Marker embedding rows start at the mean of the trained rows (training plan B8, F46)."""

from __future__ import annotations

import os
from typing import Any

import pytest
import torch

from sieve.model.data import add_markers, init_marker_rows
from sieve.serialise import MARKERS

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _tokenizer() -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained("prajjwal1/bert-tiny")


def _bert(vocab: int) -> Any:
    from transformers import BertConfig, BertForSequenceClassification

    return BertForSequenceClassification(
        BertConfig(
            vocab_size=vocab,
            hidden_size=16,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=32,
        )
    )


def _modernbert(vocab: int) -> Any:
    from transformers import ModernBertConfig, ModernBertForSequenceClassification

    config = ModernBertConfig(
        vocab_size=vocab, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=32, pad_token_id=0,
    )  # fmt: skip
    return ModernBertForSequenceClassification(config)


@pytest.mark.parametrize("build", [_bert, _modernbert], ids=["bert", "modernbert"])
@pytest.mark.parametrize("spare_rows", [0, 90], ids=["grows", "deberta-like-spare-rows"])
def test_marker_rows_are_the_mean_of_the_trained_rows(build: Any, spare_rows: int) -> None:
    torch.manual_seed(0)
    tokenizer = _tokenizer()
    trained = len(tokenizer)
    model = build(trained + spare_rows)
    add_markers(tokenizer)
    init_marker_rows(model, tokenizer, trained_rows=trained)

    weight = model.get_input_embeddings().weight
    assert len(tokenizer) <= weight.shape[0]
    assert weight.shape[0] == max(len(tokenizer), trained + spare_rows)  # never shrinks
    mean = weight[:trained].mean(dim=0)
    for marker_id in tokenizer.convert_tokens_to_ids(list(MARKERS)):
        row_norm = float(weight[marker_id].norm())
        assert row_norm == pytest.approx(float(mean.norm()), rel=0.05)
        assert torch.allclose(weight[marker_id], mean)
