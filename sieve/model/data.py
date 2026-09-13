"""Per-fold grouped dataset construction over the development split only.

`build_splits` is the single door between the corpus and any training code. It drops every
row whose `split` is not `"dev"`, so the holdout and the `real_poisoned` slice are unreachable
from training by construction rather than by convention (PRD §11.6, §9.7).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from datasets import Dataset, DatasetDict  # type: ignore[import-untyped]

from sieve.serialise import MARKERS, serialise
from sieve.types import LABELS, CorpusRecord, Label, ScannerFinding

log = logging.getLogger(__name__)

LABEL2ID: dict[Label, int] = {label: index for index, label in enumerate(LABELS)}
ID2LABEL: dict[int, Label] = {index: label for label, index in LABEL2ID.items()}

COLUMNS: tuple[str, ...] = (
    "id",
    "text",
    "label",
    "difficulty",
    "concealment",
    "source",
    "server_name",
    "fold",
)

Mode = Literal["full", "description_only"]


class _Tokenizer(Protocol):
    """The slice of the `transformers` tokenizer API `add_markers` needs."""

    def add_special_tokens(self, special_tokens_dict: dict[str, Any]) -> int: ...

    def __len__(self) -> int: ...


def load_corpus(path: Path) -> list[CorpusRecord]:
    """Read a JSONL corpus file. Blank lines are skipped; every other line must parse."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [CorpusRecord.model_validate_json(line) for line in lines if line.strip()]


def add_markers(tokenizer: _Tokenizer) -> int:
    """Add the field markers as additional special tokens; return the new vocabulary size.

    Idempotent: a tokenizer that already carries the markers is unchanged and the same size
    comes back, so a resumed run cannot silently grow the embedding matrix twice.
    """
    tokenizer.add_special_tokens({"additional_special_tokens": list(MARKERS)})
    return len(tokenizer)


def _row(
    record: CorpusRecord,
    *,
    mode: Mode,
    scanner_findings_by_id: dict[str, list[ScannerFinding]] | None,
) -> dict[str, Any]:
    findings = (scanner_findings_by_id or {}).get(record.id)
    return {
        "id": record.id,
        "text": serialise(record.to_tool_record(), mode=mode, scanner_findings=findings),
        "label": LABEL2ID[record.label],
        "difficulty": record.difficulty,
        "concealment": record.concealment,
        "source": record.source,
        "server_name": record.server_name,
        "fold": record.fold,
    }


def _dataset(rows: list[dict[str, Any]]) -> Dataset:
    if not rows:
        return Dataset.from_dict({name: [] for name in COLUMNS})
    return Dataset.from_list(rows)


def _early_stop_servers(
    rows_by_server: dict[str, list[CorpusRecord]], fraction: float, seed: int
) -> set[str]:
    """Pick whole servers, seeded and order-independent, until `fraction` of rows is covered.

    Whole servers, never rows: an early-stopping slice that shares a server with the training
    rows would early-stop on text the model has already seen.
    """
    servers = sorted(rows_by_server)
    if len(servers) < 2:
        raise ValueError(
            f"a fold needs at least two training servers to carve an early-stopping slice, "
            f"got {len(servers)}"
        )
    total = sum(len(rows_by_server[s]) for s in servers)
    target = max(1, round(fraction * total))
    order = [str(s) for s in np.random.default_rng(seed).permutation(np.asarray(servers))]
    picked: set[str] = set()
    covered = 0
    for server in order:
        if len(picked) == len(servers) - 1:
            break
        picked.add(server)
        covered += len(rows_by_server[server])
        if covered >= target:
            break
    return picked


def build_splits(
    records: Sequence[CorpusRecord],
    *,
    fold: int,
    mode: Mode = "full",
    scanner_findings_by_id: dict[str, list[ScannerFinding]] | None = None,
    early_stop_fraction: float = 0.10,
    seed: int = 0,
) -> DatasetDict:
    """Splits for ONE fold of the grouped 5-fold CV over `split == "dev"` records.

    Returns `train` (dev servers whose fold != `fold`, minus the early-stopping slice),
    `early_stop` (a `early_stop_fraction` slice of those same TRAINING servers, grouped),
    and `oof` (dev servers whose fold == `fold`). Rows with `split != "dev"` are dropped:
    the holdout and the real_poisoned slice are unreachable from training code by construction.
    """
    dev = [r for r in records if r.split == "dev"]
    dropped = len(records) - len(dev)
    if dropped:
        log.info("dropped %d row(s) whose split is not 'dev'", dropped)

    held_out = [r for r in dev if r.fold == fold]
    training_pool = [r for r in dev if r.fold != fold]

    rows_by_server: dict[str, list[CorpusRecord]] = defaultdict(list)
    for record in training_pool:
        rows_by_server[record.server_name].append(record)
    early_servers = _early_stop_servers(dict(rows_by_server), early_stop_fraction, seed)

    def rows(source: Sequence[CorpusRecord]) -> list[dict[str, Any]]:
        return [
            _row(r, mode=mode, scanner_findings_by_id=scanner_findings_by_id)
            for r in sorted(source, key=lambda r: r.id)
        ]

    train = [r for r in training_pool if r.server_name not in early_servers]
    early = [r for r in training_pool if r.server_name in early_servers]
    return DatasetDict(
        {
            "train": _dataset(rows(train)),
            "early_stop": _dataset(rows(early)),
            "oof": _dataset(rows(held_out)),
        }
    )


def texts_and_labels(dataset: Dataset) -> tuple[list[str], list[int]]:
    """The two columns every classifier in this lane consumes, in row order."""
    return list(dataset["text"]), [int(x) for x in dataset["label"]]
