"""Trainer callbacks for the `--fold all` recipe (training plan B9, finding F45)."""

from __future__ import annotations

from typing import Any

from transformers import TrainerCallback


class StopAfterEpochs(TrainerCallback):
    """Stop at the end of epoch `n`, leaving the LR schedule's horizon where the profile set it.

    `--fold all` used to train `num_train_epochs = n`, so its linear schedule decayed to 0 at
    epoch n while the CV models it stands in for were still at (1 - n/E) of their peak at their
    best epoch. Keeping the horizon at the profile's E and stopping here puts the whole-dev model
    at the same point of the same schedule.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.stopped_at_step: int | None = None

    def on_epoch_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        if state.epoch is not None and state.epoch >= self.n - 1e-9:
            control.should_training_stop = True
            self.stopped_at_step = int(state.global_step)
        return control


class LearningRateAtEpochEnd(TrainerCallback):
    """The learning rate the scheduler holds at the end of every epoch, for `metrics.json`."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def on_epoch_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        scheduler = kwargs.get("lr_scheduler")
        if scheduler is not None:
            self.values.append(float(scheduler.get_last_lr()[0]))
        return control


class StepTimer(TrainerCallback):
    """Wall seconds of every optimizer step, for `make gpu-bench` (training plan C3).

    Measured end to end between consecutive `on_step_end`s (from `on_train_begin` for the
    first), so batch fetching, the accumulation window and the optimizer step are all inside.
    On CUDA it synchronises first: kernels are queued asynchronously, and without the sync a
    step's time would land on whichever later step happened to wait for it.
    """

    def __init__(self) -> None:
        self.values: list[float] = []
        self._last: float | None = None

    @staticmethod
    def _now() -> float:
        import time

        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        return time.perf_counter()

    def on_train_begin(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        self._last = self._now()
        return control

    def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        now = self._now()
        if self._last is not None:
            self.values.append(now - self._last)
        self._last = now
        return control
