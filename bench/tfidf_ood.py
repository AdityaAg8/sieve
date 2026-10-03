"""`make tfidf-ood` (training plan C5, §6.1): the repo's TF-IDF baseline on val-OOD.

Only this number is quoted for TF-IDF out of distribution: `sieve/model/tfidf.py` (word 1-2 +
char_wb 3-5-grams, C = 4, full serialisation; ablation row 2), fitted on every dev row, scoring
the val-OOD tools. The plan-evidence scratch TF-IDF is a diagnostic and is never quoted.

val-OOD is report-only: nothing here chooses a model or a threshold. The sealed test-OOD sets are
scored with the same `score()` by `bench/eval_final.py` under `SIEVE_FINAL=1`, never from here.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from bench.collect.ood_rows import OodRow, load_rows
from sieve.model.data import LABEL2ID, load_corpus
from sieve.model.tfidf import TfidfClassifier
from sieve.serialise import serialise
from sieve.types import LABELS

POISONED = LABELS.index("poisoned")


def fit_dev(corpus: Path) -> TfidfClassifier:
    dev = [r for r in load_corpus(corpus) if r.split == "dev"]
    texts = [serialise(r.to_tool_record(), mode="full") for r in dev]
    return TfidfClassifier().fit(texts, [LABEL2ID[r.label] for r in dev])


def _block(rows: Sequence[OodRow], scores: np.ndarray) -> dict[str, Any]:
    truth = np.array([r.poisoned for r in rows])
    both = 0 < truth.sum() < len(truth)
    positives = scores[truth]
    return {
        "n": len(rows),
        "n_poisoned": int(truth.sum()),
        "auroc": float(roc_auc_score(truth, scores)) if both else None,
        "recall_at_half": float(np.mean(positives >= 0.5)) if len(positives) else None,
        "fpr_at_half": float(np.mean(scores[~truth] >= 0.5)) if (~truth).any() else None,
    }


def score(model: TfidfClassifier, rows: Sequence[OodRow]) -> dict[str, Any]:
    """Poisoned-column AUROC overall and per source (None where a source has one class)."""
    scores = model.predict_proba([r.text() for r in rows])[:, POISONED]
    by_source: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_source[r.source].append(i)
    return {
        **_block(rows, scores),
        "per_source": {
            source: _block([rows[i] for i in idx], scores[idx])
            for source, idx in sorted(by_source.items())
        },
    }


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="python -m bench.tfidf_ood")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--val-ood", type=Path, default=Path("bench/data/val_ood.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("bench/results/tfidf_ood.json"))
    ns = parser.parse_args(argv)
    if not ns.val_ood.exists():
        raise SystemExit(
            f"{ns.val_ood} does not exist; reconstruct it with `python -m bench.collect.val_ood` "
            "(training plan G3) first"
        )
    model = fit_dev(ns.corpus)
    report = {
        "model": "sieve/model/tfidf.py (word 1-2 + char_wb 3-5, C=4, full serialisation)",
        "fitted_on": {"corpus": ns.corpus.as_posix(), "split": "dev"},
        "val_ood": score(model, load_rows(ns.val_ood)),
        "test_ood": "scored only by bench/eval_final.py under SIEVE_FINAL=1",
    }
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["val_ood"], indent=2))
    return report


if __name__ == "__main__":  # pragma: no cover
    main()
