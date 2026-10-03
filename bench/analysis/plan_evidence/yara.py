"""Cisco mcp-scanner YARA rates per (source, class) (plan §3.1).

Static mode over a `tools/list` JSON, nothing executed. Tools are renamed `t<i>` so a result maps
back to exactly one pool row: joining by the real tool name is the F58 bug (`C5`).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from bench.analysis.plan_evidence.pool import EMPTY_SCHEMA, Row

Runner = Callable[[str, list[str]], str]


def _run(binary: str, args: list[str]) -> str:
    from sieve.adapters._scanner_common import run_scanner

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done: subprocess.CompletedProcess[str] = run_scanner(binary, args, env=env)
    return done.stdout


def scan_flags(
    rows: Sequence[Row], workdir: Path, *, runner: Runner = _run, batch: int = 2000
) -> list[bool]:
    """True where YARA flags the row (`is_safe` false)."""
    workdir.mkdir(parents=True, exist_ok=True)
    flags: list[bool] = []
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        tools = [
            {"name": f"t{i}", "description": r["desc"], "inputSchema": r["schema"] or EMPTY_SCHEMA}
            for i, r in enumerate(chunk)
        ]
        path = workdir / f"scan_in_{start}.json"
        path.write_text(json.dumps({"tools": tools}, ensure_ascii=False), encoding="utf-8")
        out = runner(
            "mcp-scanner", ["--analyzers", "yara", "--raw", "static", "--tools", str(path)]
        )
        results: dict[str, Any] = {r["tool_name"]: r for r in json.loads(out[out.index("[") :])}
        flags += [not results[f"t{i}"]["is_safe"] for i in range(len(chunk))]
    return flags


def rates(rows: Sequence[Row], flags: Sequence[bool]) -> dict[tuple[str, str], tuple[int, int]]:
    """(source, class) -> (flagged, total)."""
    total: Counter[tuple[str, str]] = Counter()
    flagged: Counter[tuple[str, str]] = Counter()
    for r, f in zip(rows, flags, strict=True):
        key = (r["src"], r["cls"])
        total[key] += 1
        flagged[key] += int(f)
    return {k: (flagged[k], total[k]) for k in sorted(total)}


def format_rates(table: dict[tuple[str, str], tuple[int, int]]) -> list[str]:
    return [f"YARA {src} / {cls}: {f}/{n} = {f / n:.1%}" for (src, cls), (f, n) in table.items()]
