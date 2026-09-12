"""TF-IDF + logistic regression: the trivial baseline row 2 of every ablation table.

It exists to make the encoder earn its place. Word 1-2-grams catch the lexical giveaways,
char 3-5-grams catch obfuscated spellings, and if this wins a bucket that is the result.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

import numpy as np
import numpy.typing as npt
from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import FeatureUnion, Pipeline  # type: ignore[import-untyped]

from sieve.model.thresholds import UNCALIBRATED
from sieve.model.version import git_sha
from sieve.protocols import Thresholds
from sieve.types import LABELS

MODEL_FILE = "tfidf-lr.joblib"
CONFIG_FILE = "sieve_config.json"


def _build() -> Pipeline:
    features = FeatureUnion(
        [
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1
                ),
            ),
        ]
    )
    classifier = LogisticRegression(class_weight="balanced", C=4.0, max_iter=2000)
    return Pipeline([("features", features), ("lr", classifier)])


class TfidfClassifier:
    """Satisfies `sieve.protocols.Classifier`. Columns are in `sieve.types.LABELS` order."""

    def __init__(self, *, thresholds: Thresholds | None = None) -> None:
        self.model_version = f"tfidf-lr@{git_sha()}"
        self.thresholds = thresholds if thresholds is not None else UNCALIBRATED
        self._pipeline: Pipeline | None = None

    def fit(self, texts: Sequence[str], labels: Sequence[int]) -> Self:
        pipeline = _build()
        pipeline.fit(list(texts), list(labels))
        self._pipeline = pipeline
        return self

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        if self._pipeline is None:
            raise RuntimeError("TfidfClassifier is not fitted; call fit() or load() first")
        raw = np.asarray(self._pipeline.predict_proba(list(texts)), dtype=np.float64)
        # `predict_proba` columns follow `classes_`, which is the sorted set of labels SEEN in
        # training. A fold missing a class would silently shift every column, so reproject.
        out = np.zeros((raw.shape[0], len(LABELS)), dtype=np.float64)
        for column, label_id in enumerate(self._pipeline.named_steps["lr"].classes_):
            out[:, int(label_id)] = raw[:, column]
        return out

    def save(self, directory: Path) -> Path:
        import joblib  # type: ignore[import-untyped]

        if self._pipeline is None:
            raise RuntimeError("refusing to save an unfitted TfidfClassifier")
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._pipeline, directory / MODEL_FILE)
        config: dict[str, Any] = {
            "model_version": self.model_version,
            "temperature": self.thresholds.temperature,
            "t_low": _nullable(self.thresholds.t_low),
            "t_high": _nullable(self.thresholds.t_high),
            "val_recall_at_t_low": _nullable(self.thresholds.val_recall_at_t_low),
            "val_precision_at_t_high": _nullable(self.thresholds.val_precision_at_t_high),
            "routing_rate": _nullable(self.thresholds.routing_rate),
        }
        (directory / CONFIG_FILE).write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        return directory

    @classmethod
    def load(cls, directory: Path) -> TfidfClassifier:
        # joblib unpickles, so `directory` must be an artefact this repo produced (a `bench`
        # run or a CI job), never a user upload. Nothing in Sieve loads a baseline from a
        # scan input; the only caller is the offline ablation runner.
        import joblib

        config = json.loads((directory / CONFIG_FILE).read_text(encoding="utf-8"))
        model = cls()
        model.model_version = str(config["model_version"])
        model._pipeline = joblib.load(directory / MODEL_FILE)
        return model


def _nullable(value: float) -> float | None:
    """NaN is not JSON; `null` is, and `thresholds.load` already refuses a null."""
    return None if np.isnan(value) else float(value)
