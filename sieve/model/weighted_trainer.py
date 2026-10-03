"""The one training loss for every architecture (training plan B7, finding F15).

transformers 4.57.6 picks its loss normalisation from the model's `forward` signature: a model
whose `forward` takes `**kwargs` (ModernBERT) receives `num_items_in_batch` and was trained on
Σwℓ/N, a model without it (DeBERTa) on Σwℓ/Σw per micro-batch. That confounded the architecture
comparison. Here `model_accepts_loss_kwargs` is forced off and Σw is taken over the whole
gradient-accumulation window, so each micro-batch contributes Σwℓ / Σw_window and the window's
gradient is exactly that of `CrossEntropyLoss(weight, reduction="mean")` over the effective
batch, for both architectures, with or without accumulation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import torch
from transformers import Trainer


class WeightedTrainer(Trainer):
    def __init__(
        self, *args: Any, class_weights: Sequence[float] | None, num_labels: int, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.model_accepts_loss_kwargs = False
        self.class_weights = None if class_weights is None else list(class_weights)
        self.num_labels = num_labels
        self._window_weight: torch.Tensor | None = None

    def _weights(self, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        if self.class_weights is None:
            return torch.ones(self.num_labels, device=device, dtype=dtype)
        return torch.tensor(self.class_weights, device=device, dtype=dtype)

    def _get_num_items_in_batch(
        self, batch_samples: list[Any], device: torch.device
    ) -> torch.Tensor | int | None:
        """Record Σw over the accumulation window; return None so HF divides by the steps."""
        labels = [b["labels"] for b in batch_samples if "labels" in b]
        if labels:
            joined = torch.cat([x.view(-1) for x in labels]).to(device)
            self._window_weight = self._weights(device, torch.float32)[joined].sum()
        return None

    def compute_loss(
        self,
        model: Any,
        inputs: dict[str, Any],
        return_outputs: bool = False,
        num_items_in_batch: Any = None,
    ) -> Any:
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.get("logits").view(-1, self.num_labels)
        labels = labels.view(-1)
        weights = self._weights(logits.device, logits.dtype)
        per_row = torch.nn.functional.cross_entropy(logits, labels, reduction="none")
        weighted = (per_row * weights[labels]).sum()
        if model.training and self._window_weight is not None:
            # `training_step` divides by the accumulation steps; multiply them back in so the
            # window sums to Σwℓ / Σw_window.
            steps = getattr(self, "current_gradient_accumulation_steps", 1)
            loss = weighted / self._window_weight.to(logits.dtype) * steps
        else:
            loss = weighted / weights[labels].sum()
        return (loss, outputs) if return_outputs else loss
