"""Shared fixtures for the corpus-tooling tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from sieve.types import CorpusRecord

MINI = Path("fixtures/corpus/mini-50.jsonl")


@pytest.fixture
def mini_record() -> CorpusRecord:
    """The first record of the committed mini corpus: weather/get_current, label `benign`."""
    first = MINI.read_text(encoding="utf-8").splitlines()[0]
    return CorpusRecord.model_validate_json(first)


@pytest.fixture
def mini_records() -> list[CorpusRecord]:
    lines = MINI.read_text(encoding="utf-8").splitlines()
    return [CorpusRecord.model_validate_json(line) for line in lines]
