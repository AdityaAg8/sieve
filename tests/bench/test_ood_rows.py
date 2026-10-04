"""Spec-invalid OOD tools are excluded by id, never a crash mid-opening (ADR-009)."""

from __future__ import annotations

from typing import Any

from bench.collect.ood_rows import OodRow, split_spec_valid


def _row(row_id: str, tool: dict[str, Any]) -> OodRow:
    return OodRow(id=row_id, source="docker", server_name="srv", tool=tool, poisoned=False)


def test_split_spec_valid_keeps_valid_tools_and_names_the_rest() -> None:
    good = _row("ok", {"name": "read_file", "description": "Reads a file.", "inputSchema": {}})
    zero_width = _row("zw", {"name": "read_file​", "description": "x", "inputSchema": {}})
    kept, excluded = split_spec_valid([good, zero_width])
    assert [r.id for r in kept] == ["ok"]
    assert excluded == ["zw"]


def test_a_tool_whose_keys_are_not_strings_is_excluded_not_raised() -> None:
    """Review P3: `ToolDefinition(**tool)` raises TypeError on a non-str key."""
    odd = _row("odd", {"name": "t", "description": "d", "inputSchema": {}, 1: "x"})  # type: ignore[dict-item]
    kept, excluded = split_spec_valid([odd])
    assert kept == [] and excluded == ["odd"]
