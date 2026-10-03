"""Tokenizer-side evidence: token lengths, payload survival, tokenizer parity, early-stop slices.

Offline only: the tokenizers come from the Hugging Face cache (`HF_HUB_OFFLINE=1` is set by the
CLI). The *length-only probe* is defined here once, as the model sees it: the token count of the
full serialisation under the DeBERTa tokenizer with the field markers added (plan §10, F22).
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np

from sieve.serialise import param_lines, serialise
from sieve.types import CorpusRecord

DEBERTA = "microsoft/deberta-v3-base"
MODERNBERT = "answerdotai/ModernBERT-base"


def tokenizer(name: str, *, use_fast: bool = True) -> Any:
    from transformers import AutoTokenizer

    from sieve.model.data import add_markers

    tok = AutoTokenizer.from_pretrained(name, use_fast=use_fast)
    add_markers(tok)
    return tok


def full_text(record: CorpusRecord) -> str:
    return serialise(record.to_tool_record(), mode="full")


def row_text(row: dict[str, Any]) -> str:
    """A pool row (no title, siblings or annotations) serialised the same way."""
    lines = [f"[NAME] {row['name']}"]
    if row["desc"]:
        lines.append(f"[DESC] {row['desc']}")
    with contextlib.suppress(TypeError, ValueError, AttributeError):
        lines += [f"[PARAM] {p}" for p in param_lines(row["schema"] or {})]
    return "\n".join(lines)


def token_lengths(tok: Any, texts: Sequence[str]) -> np.ndarray:
    return np.array([len(tok(t)["input_ids"]) for t in texts])


def serialised_token_length(records: Sequence[CorpusRecord], tok: Any) -> np.ndarray:
    """The length-only probe's one feature (untruncated, so it can exceed 512)."""
    return token_lengths(tok, [full_text(r) for r in records])


def length_summary(n: np.ndarray) -> dict[str, float]:
    return {
        "n": int(len(n)),
        "p50": int(np.percentile(n, 50)),
        "p95": int(np.percentile(n, 95)),
        "p99": int(np.percentile(n, 99)),
        "max": int(n.max()),
        "over_256": float(np.mean(n > 256)),
        "over_512": float(np.mean(n > 512)),
        "over_1024": float(np.mean(n > 1024)),
    }


def length_only_auroc(dev: Sequence[CorpusRecord], lengths: np.ndarray) -> dict[str, float]:
    """AUROC of the length feature alone, on dev rows (higher = longer is the positive class)."""
    from sklearn.metrics import roc_auc_score

    labels = np.array([r.label for r in dev])
    keep = labels != "poisoned"
    return {
        "imperative_vs_benign": float(
            roc_auc_score(labels[keep] == "imperative_benign", lengths[keep])
        ),
        "poisoned_vs_rest": float(roc_auc_score(labels == "poisoned", lengths)),
    }


def survival(
    records: Sequence[CorpusRecord], tok: Any, lengths: Sequence[int] = (256, 384, 512)
) -> dict[int, int]:
    from sieve.model.train import payload_survival_failures

    poisoned = [r for r in records if r.label == "poisoned"]
    return {
        L: len(payload_survival_failures(poisoned, tok, max_length=L, mode="full")) for L in lengths
    }


def tokenizer_parity(
    records: Sequence[CorpusRecord], *, n: int = 400, seed: int = 0
) -> dict[str, Any]:
    """Fast vs slow DeBERTa ids on `n` seeded corpus rows, and the rows holding `[UNK]` (F13)."""
    fast, slow = tokenizer(DEBERTA), tokenizer(DEBERTA, use_fast=False)
    rng = np.random.default_rng(seed)
    sample = [
        records[i] for i in rng.choice(len(records), size=min(n, len(records)), replace=False)
    ]
    same = sum(fast(full_text(r))["input_ids"] == slow(full_text(r))["input_ids"] for r in sample)
    unk = [r for r in records if fast.unk_token_id in fast(full_text(r))["input_ids"]]
    return {
        "rows": len(sample),
        "identical_ids": same,
        "parity": same / len(sample),
        "unk_rows": len(unk),
        "unk_by_source_label": dict(Counter(f"{r.source}/{r.label}" for r in unk)),
    }


def early_stop_slices(records: Sequence[CorpusRecord], seed: int = 0) -> dict[int, int]:
    """Rows in each fold's early-stopping slice (F14)."""
    from sieve.model.data import build_splits

    return {k: len(build_splits(records, fold=k, seed=seed)["early_stop"]) for k in range(5)}
