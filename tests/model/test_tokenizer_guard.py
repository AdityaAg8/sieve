"""The DeBERTa tokenizer warning is a false positive (training plan B6, F13)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pytest

from sieve.model.tokenizer_guard import TOKENIZER_NOTE, collapse_unknown, parity

pytestmark = [
    pytest.mark.filterwarnings("ignore::FutureWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::UserWarning"),
]

LABELLED = Path("bench/data/labelled.jsonl")
DEBERTA = "microsoft/deberta-v3-base"


def test_a_fast_unknown_equals_a_slow_byte_fallback_run() -> None:
    fast = ["▁a", "[UNK]", "b"]
    slow = ["▁a", "<0xE2>", "<0x80>", "<0xAE>", "b"]
    assert collapse_unknown(fast) == collapse_unknown(slow) == ["▁a", "[UNK]", "b"]


def test_other_differences_are_not_collapsed() -> None:
    assert collapse_unknown(["▁a", "b"]) != collapse_unknown(["▁ab"])


def test_parity_counts_raw_and_unknown_aware_agreement() -> None:
    pairs = [(["a"], ["a"]), (["[UNK]"], ["<0xE2>", "<0x80>"]), (["a"], ["b"])]
    got = parity(pairs)
    assert (got.rows, got.identical, got.equal_modulo_unknown) == (3, 1, 2)
    assert got.other_differences == 1


def test_training_never_passes_fix_mistral_regex() -> None:
    for path in Path("sieve").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "fix_mistral_regex=" not in text, path


def test_the_note_names_the_warning_and_the_finding() -> None:
    assert "fix_mistral_regex" in TOKENIZER_NOTE and "B6" in TOKENIZER_NOTE
    assert "\n" not in TOKENIZER_NOTE


@pytest.mark.slow
@pytest.mark.skipif(not LABELLED.exists(), reason="needs the gitignored corpus")
def test_fast_and_slow_deberta_agree_on_400_corpus_rows(caplog: pytest.LogCaptureFixture) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    from transformers import AutoTokenizer

    from sieve.model.data import add_markers, load_corpus
    from sieve.serialise import serialise

    records = load_corpus(LABELLED)
    chosen = np.random.default_rng(0).choice(len(records), size=400, replace=False)
    texts = [serialise(records[int(i)].to_tool_record()) for i in chosen]
    fast = AutoTokenizer.from_pretrained(DEBERTA)
    slow = AutoTokenizer.from_pretrained(DEBERTA, use_fast=False)
    for tok in (fast, slow):
        add_markers(tok)
    pairs = [
        (
            fast.convert_ids_to_tokens(fast(t)["input_ids"]),
            slow.convert_ids_to_tokens(slow(t)["input_ids"]),
        )
        for t in texts
    ]
    got = parity(pairs)
    logging.getLogger("test").info("raw parity %.4f", got.identical / got.rows)
    assert got.rows == 400
    assert got.equal_modulo_unknown / got.rows >= 0.99
    assert got.other_differences == 0
