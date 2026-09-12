"""Local grouped-fold assignment so the model lane does not import L1's bench.split."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from sieve.types import CorpusRecord

# pyproject turns every warning into an error. `huggingface_hub` warns once per cold cache that
# Windows cannot make symlinks, `transformers` swallows the resulting exception and re-raises a
# misleading "can't load the configuration" OSError. Silence it at the source, before any import
# of the hub triggers a download.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _fold_of(server_name: str, folds: int = 5) -> int:
    # Deterministic across processes and platforms; hash() is salted per run and must not be used.
    digest = hashlib.sha256(server_name.encode("utf-8")).hexdigest()
    return int(digest, 16) % folds


def _mini_50() -> list[CorpusRecord]:
    lines = Path("fixtures/corpus/mini-50.jsonl").read_text(encoding="utf-8").splitlines()
    return [CorpusRecord.model_validate_json(line) for line in lines]


@pytest.fixture
def dev_records() -> list[CorpusRecord]:
    """mini-50 with folds re-assigned BY SERVER, which the fixture's own fold field is not."""
    out = []
    for rec in _mini_50():
        out.append(rec.model_copy(update={"split": "dev", "fold": _fold_of(rec.server_name)}))
    return out


@pytest.fixture
def balanced_dev_records() -> list[CorpusRecord]:
    """`dev_records`, but every one of the five folds is guaranteed non-empty.

    `_fold_of` over mini-50's seven server names lands nothing in fold 0 and four servers in
    fold 2, so it cannot exercise "every fold has three non-empty splits". Round-robin over the
    sorted server names is still a *grouped* split (a server is never split across folds) and
    keeps every fold populated. Reported in docs/handoffs/L3.md.
    """
    records = _mini_50()
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    return [r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}) for r in records]
