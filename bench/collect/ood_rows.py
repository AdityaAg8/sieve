"""Binary out-of-distribution eval rows (training plan C5, G2, G3).

val-OOD and test-OOD tools are not `CorpusRecord`s: they carry a source label (poisoned or not),
never a three-class one, and they are reconstructed from pinned upstream pointers rather than
committed. One small bench-local record, read and written here, keeps every collector and every
scorer on the same shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sieve.serialise import serialise
from sieve.types import ToolDefinition, ToolRecord


@dataclass(frozen=True)
class OodRow:
    id: str
    source: str
    server_name: str
    tool: dict[str, Any]  # name, description, inputSchema, as the upstream source gives them
    poisoned: bool
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def is_synthetic(self) -> bool:
        """OOD rows are third-party text; `write_rows` refuses one that is not (G5)."""
        return self.source == "synthetic_mutation"

    @property
    def spec_valid(self) -> bool:
        """Whether the tool passes `ToolDefinition`, e.g. MCP's name rule (`TOOL_NAME_RE`).

        Sieve's ingest rejects any other tool, so it never reaches Stage 1. Upstream sets carry a
        few: a zero-width character appended to a name is itself an attack technique.
        """
        try:
            ToolDefinition(**self.tool)
        except (ValidationError, TypeError):
            return False
        return True

    def tool_record(self) -> ToolRecord:
        return ToolRecord(
            id=self.id, server_name=self.server_name, tool=ToolDefinition(**self.tool)
        )

    def text(self) -> str:
        """The full serialisation, exactly as a corpus tool is serialised for the models."""
        return serialise(self.tool_record(), mode="full")


def load_rows(path: Path) -> list[OodRow]:
    return [
        OodRow(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def split_spec_valid(rows: Iterable[OodRow]) -> tuple[list[OodRow], list[str]]:
    """(the rows a scorer can score, the ids of those ingest would reject).

    Scorers keep the second group out of every metric and report it by id: never a crash
    halfway through a one-shot opening, and never a silent drop (ADR-009).
    """
    kept: list[OodRow] = []
    excluded: list[str] = []
    for row in rows:
        if row.spec_valid:
            kept.append(row)
        else:
            excluded.append(row.id)
    return kept, excluded


def write_rows(path: Path, rows: Iterable[OodRow]) -> Path:
    rows = list(rows)
    synthetic = [r.id for r in rows if r.is_synthetic]
    if synthetic:
        raise ValueError(f"no test-OOD or val-OOD row may be synthetic: {synthetic[:5]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(asdict(r), ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
        newline="\n",
    )
    return path


__all__ = ["OodRow", "load_rows", "split_spec_valid", "write_rows"]
