"""One normalised pool of tool texts from every source (plan §3.1, §5.2, §6.1).

`main_pool` is the 12,212-tool pool the YARA re-measurement scanned (corpus, catalog, MCPTox
poisoned and clean, guardbench `tools/list` cases, MSB); `extra_pool` is the 969 tools scanned
second (Docker registry, scannerbench, MTP). Each row is a plain dict:
`{src, cls, server, name, desc, schema, ...}`. Python sources are read with `ast`, never run.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sieve.types import CorpusRecord

Row = dict[str, Any]
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _row(
    src: str, cls: str, server: str, name: str, desc: str | None, schema: Any = None, **extra: Any
) -> Row:
    return {
        "src": src,
        "cls": cls,
        "server": server,
        "name": name,
        "desc": desc or "",
        "schema": schema if isinstance(schema, dict) else EMPTY_SCHEMA,
        **extra,
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def corpus_rows(records: Iterable[CorpusRecord]) -> list[Row]:
    return [
        _row(
            f"corpus:{r.source}",
            r.label,
            r.server_name,
            r.tool.name,
            r.tool.description,
            r.tool.inputSchema,
            split=r.split,
            fold=r.fold,
            gen=r.generator_model,
            rid=r.id,
            mutation_of=r.mutation_of,
        )
        for r in records
    ]


def catalog_rows(table: Iterable[dict[str, Any]]) -> list[Row]:
    """Catalog parquet rows, one per unique (server, tool)."""
    out: list[Row] = []
    seen: set[tuple[Any, Any]] = set()
    for r in table:
        key = (r.get("server_name") or r.get("package"), r.get("tool_name") or r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        schema = r.get("input_schema")
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                schema = None
        out.append(
            _row(
                "catalog",
                "unlabelled",
                key[0],
                key[1],
                r.get("tool_description"),
                schema,
                package=r.get("package"),
            )
        )
    return out


def mcptox_rows(root: Path) -> list[Row]:
    """MCPTox `pure_tool.json`: 485 poisoned descriptions, with template and risk class."""
    out: list[Row] = []
    for entry in _read_json(root / "pure_tool.json"):
        for v in entry.values():
            out.append(
                _row(
                    "mcptox",
                    "poisoned",
                    v["server_name"],
                    v["tool_name"],
                    v["tool_content"].strip(),
                    template=v["paradigm"],
                    risk=v["security risk"],
                )
            )
    return out


_CLEAN_TOOL = re.compile(r"Tool: (.+?)\nDescription: (.*?)\nArguments:", re.S)


def mcptox_clean_rows(root: Path) -> list[Row]:
    """Clean tools from MCPTox's per-server clean system prompts, unique per (server, tool)."""
    out: list[Row] = []
    seen: set[tuple[str, str]] = set()
    for server, v in _read_json(root / "response_all.json")["servers"].items():
        for m in _CLEAN_TOOL.finditer(v["clean_system_promot"]):
            if (server, m.group(1)) in seen:
                continue
            seen.add((server, m.group(1)))
            out.append(
                _row("mcptox_clean", "benign_unlabelled", server, m.group(1), m.group(2).strip())
            )
    return out


_GB_BUCKET = {"attacks": "attack_case", "benign": "benign_case", "warn": "warn_case"}


def guardbench_rows(root: Path) -> list[Row]:
    """guardbench cases whose message is a `tools/list` result: one row per tool."""
    out: list[Row] = []
    for path in sorted((root / "cases").rglob("*.json")):
        if path.name == "index.json":
            continue
        case = _read_json(path)
        message = case.get("message")
        result = message.get("result", {}) if isinstance(message, dict) else {}
        tools = result.get("tools") if isinstance(result, dict) else None
        for t in tools or []:
            out.append(
                _row(
                    "guardbench",
                    _GB_BUCKET[case["bucket"]],
                    case["id"],
                    t.get("name"),
                    t.get("description"),
                    t.get("inputSchema"),
                    case=case["id"],
                )
            )
    return out


def _decorated_tools(path: Path) -> list[tuple[str, str]]:
    """(name, docstring) for every function decorated with something mentioning `tool`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (n.name, ast.get_docstring(n) or "")
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and any("tool" in ast.unparse(d) for d in n.decorator_list)
    ]


def msb_rows(root: Path) -> list[Row]:
    """MSB attack servers: clean tools, plus each attack file's changed description variants."""
    out: list[Row] = []
    for server_dir in sorted(
        p for p in (root / "data/tools/attack_tools").glob("*/*") if p.is_dir()
    ):
        files = sorted(server_dir.glob("*.py"))
        base = [f for f in files if "-" not in f.name]
        base_docs = {nd for f in base for nd in _decorated_tools(f)}
        for f in files:
            if "-" not in f.name:
                out += [
                    _row("msb_clean", "benign_unlabelled", server_dir.name, n, d)
                    for n, d in _decorated_tools(f)
                ]
                continue
            attack = f.stem.split("-", 1)[1]
            out += [
                _row("msb", f"attack_variant:{attack}", server_dir.name, n, d)
                for n, d in _decorated_tools(f)
                if (n, d) not in base_docs
            ]
    return out


def docker_rows(root: Path) -> list[Row]:
    out: list[Row] = []
    for path in sorted((root / "servers").glob("*/tools.json")):
        for t in _read_json(path) or []:
            out.append(
                _row(
                    "docker",
                    "benign_unlabelled",
                    path.parent.name,
                    t.get("name"),
                    t.get("description"),
                    t.get("inputSchema"),
                )
            )
    return out


def scannerbench_rows(root: Path) -> list[Row]:
    out: list[Row] = []
    labels = root / "data/labels.jsonl"
    for line in labels.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        meta = json.loads(line)
        manifest = _read_json(root / "data" / meta["file"])
        for t in manifest.get("tools", []):
            out.append(
                _row(
                    f"scannerbench_{meta['language']}",
                    meta["label"],
                    meta["file"],
                    t["name"],
                    t.get("description"),
                    t.get("inputSchema"),
                )
            )
    return out


def mtp_rows(path: Path) -> list[Row]:
    out: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append(_row("mtp", "poisoned", r["id"], r["tool_name"], r["poisoned_description"]))
        out.append(_row("mtp", "benign", r["id"], r["tool_name"], r["benign_description"]))
    return out


def main_pool(
    records: Iterable[CorpusRecord], catalog: Iterable[dict[str, Any]], external: Path
) -> list[Row]:
    return (
        corpus_rows(records)
        + catalog_rows(catalog)
        + mcptox_rows(external / "MCPTox-Benchmark")
        + mcptox_clean_rows(external / "MCPTox-Benchmark")
        + guardbench_rows(external / "mcp-guardbench")
        + msb_rows(external / "MSB")
    )


def extra_pool(external: Path, mtp: Path) -> list[Row]:
    return (
        docker_rows(external / "docker-mcp-registry")
        + scannerbench_rows(external / "scannerbench")
        + mtp_rows(mtp)
    )
