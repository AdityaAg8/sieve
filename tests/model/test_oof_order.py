"""OOF predictions come back in dataset order (ADR-009).

transformers 4.57 gives `trainer.predict` a `LengthGroupedSampler` whenever `group_by_length` is
on, so v1's first sweep wrote length-grouped logits beside dataset-ordered `oof_ids.json`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from torch.utils.data import SequentialSampler

from sieve.model.train import OofOrderError, _logits_and_labels
from sieve.model.weighted_trainer import WeightedTrainer


def test_evaluation_and_prediction_never_reorder_the_dataset() -> None:
    fake_self: Any = SimpleNamespace(args=SimpleNamespace(group_by_length=True))
    sampler = WeightedTrainer._get_eval_sampler(fake_self, list(range(7)))
    assert isinstance(sampler, SequentialSampler)
    assert list(sampler) == list(range(7))


class _Predicting:
    def __init__(self, label_ids: list[int]) -> None:
        self.label_ids = label_ids

    def predict(self, dataset: Any) -> Any:
        logits = np.zeros((len(self.label_ids), 3), dtype=np.float32)
        return SimpleNamespace(predictions=logits, label_ids=np.asarray(self.label_ids))


def test_predictions_out_of_dataset_order_are_refused() -> None:
    dataset = {"labels": [0, 1, 2, 2]}
    logits, labels = _logits_and_labels(_Predicting([0, 1, 2, 2]), dataset)
    assert labels.tolist() == [0, 1, 2, 2] and logits.shape == (4, 3)
    with pytest.raises(OofOrderError):
        _logits_and_labels(_Predicting([2, 1, 0, 2]), dataset)
