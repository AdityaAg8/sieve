"""val-OOD (training plan G3, §5.1): six pinned third-party sets as report-only binary rows.

Unsealed and **report-only**: sanity checks, error analysis and the threshold-transfer
diagnostic, never model selection or a threshold (CLAUDE.md: every decision uses pooled OOF).

| source | upstream | rows | label rule (from the source's own docs) |
|---|---|---|---|
| `scannerbench_en` / `_tr` | scannerbench `data/labels.jsonl` | 83 / 81 | the file's `label` |
| `mtp` | HF `mcp-tool-poisoning` `mtp.jsonl` | 50 | poisoned / benign description of a pair |
| `guardbench` | guardbench `tools/list` cases | 37 | an attack case's poisoned tool (below) |
| `mcpsecbench` | MCPSecBench `code/*.py` | 19 | the description-level PoCs in `maliciousadd.py` |
| `when_attack` | When MCP Servers Attack `tool/*` | 28 | the `tool-metadata-*` attacks per README |

Python sources are read with `ast` only, never imported or run (PRD SC-1). A FastMCP tool's
schema is rebuilt from its signature, as FastMCP itself would. guardbench's three hidden-
character cases (`expected.action == "warn"`) carry no instruction, so their rows are marked
`indicator_only` for the report to show with and without them. When-attack's ten "Official
Baidu" impersonation tools are poisoned by the README's rule and marked `borderline` (§2.1).
Every clone is verified at its pinned commit and content hash
(`bench/data/val_ood.pointers.json`).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench.collect.ood_negatives import EMPTY_SCHEMA, OUT, Pin, content_hash, verify_clone
from bench.collect.ood_rows import OodRow, write_rows

SOURCES = ("scannerbench_en", "scannerbench_tr", "mtp", "guardbench", "mcpsecbench", "when_attack")
POINTERS = Path("bench/data/val_ood.pointers.json")
ROWS_FILE = "val_ood.jsonl"

#: guardbench attack cases that list several tools: the one the case text names as poisoned.
GUARDBENCH_NAMED = {
    "deadbugz-supply-chain-mongodb-tool-poisoning": "find",  # "via 'supplemental telemetry'"
    "owasp-mcp-1-multitool-poisoning": "poisoned",  # "only one tool poisoned"
}
#: MCPSecBench's description-level attacks: the instruction-carrying docstrings of its PoC.
MCPSECBENCH_POISONED = {
    ("maliciousadd", "add"),
    ("maliciousadd", "modify"),
    ("maliciousadd", "m_check"),
    ("maliciousadd", "aft_check"),
}
#: When-attack: only `tool-metadata-*` categories attack through the description; the README
#: names the malicious tool of each (None: every tool of the malicious server).
WHEN_METADATA_POISONED: dict[str, set[str] | None] = {
    "tool-metadata-control-flow-hijack": {"MANDATORY_sanitize_tool_output"},
    "tool-metadata-data-overcollection": {"send_email"},
    "tool-metadata-impersonation": None,
}
_PY_TYPES = {
    "int": "integer",
    "float": "number",
    "str": "string",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


class UnlabelledCaseError(ValueError):
    """A multi-tool guardbench attack case with no recorded poisoned tool."""


def _schema(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema")
    return dict(schema) if isinstance(schema, dict) else dict(EMPTY_SCHEMA)


def _tool(name: str, description: str | None, schema: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": _schema({"inputSchema": schema}),
    }


# ------------------------------------------------------------------------- JSON sources


def scannerbench_files(root: Path) -> list[Path]:
    labels = root / "data/labels.jsonl"
    metas = [json.loads(x) for x in labels.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [labels, *(root / "data" / m["file"] for m in metas)]


def scannerbench_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    files = scannerbench_files(root)
    verify_clone(root, files, commit, sha256)
    rows: list[OodRow] = []
    for line in files[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        meta = json.loads(line)
        manifest = json.loads((root / "data" / meta["file"]).read_text(encoding="utf-8"))
        for tool in manifest.get("tools", []):
            source = f"scannerbench_{meta['language']}"
            rows.append(
                OodRow(
                    id=f"val-{source}-{sum(r.source == source for r in rows):04d}",
                    source=source,
                    server_name=meta["file"],
                    tool=_tool(tool["name"], tool.get("description"), tool.get("inputSchema")),
                    poisoned=meta["label"] == "poisoned",
                    provenance={
                        "url": "https://github.com/fevziegeyurtsevenler/scannerbench",
                        "commit": commit,
                        "path": f"data/{meta['file']}",
                        "rule": f"scannerbench labels.jsonl: {meta['label']}",
                        "category": meta.get("category"),
                        "vector": meta.get("vector"),
                    },
                )
            )
    return rows


def mtp_rows(path: Path, *, revision: str, sha256: str | None = None) -> list[OodRow]:
    if sha256 is not None and hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        from bench.collect.ood_negatives import PinMismatchError

        raise PinMismatchError(f"{path} does not match its pinned sha256")
    rows: list[OodRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        pair = json.loads(line)
        for poisoned, key in ((True, "poisoned_description"), (False, "benign_description")):
            rows.append(
                OodRow(
                    id=f"val-mtp-{len(rows):04d}",
                    source="mtp",
                    server_name=pair["id"],
                    tool=_tool(pair["tool_name"], pair[key], None),
                    poisoned=poisoned,
                    provenance={
                        "url": "https://huggingface.co/datasets/fevziegeyurtsevenler/mcp-tool-poisoning",
                        "revision": revision,
                        "rule": f"mtp.jsonl `{key}`",
                    },
                )
            )
    return rows


def guardbench_files(root: Path) -> list[Path]:
    return [p for p in sorted((root / "cases").rglob("*.json")) if p.name != "index.json"]


def guardbench_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    files = guardbench_files(root)
    verify_clone(root, files, commit, sha256)
    rows: list[OodRow] = []
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        message = case.get("message")
        result = message.get("result", {}) if isinstance(message, dict) else {}
        tools = (result.get("tools") if isinstance(result, dict) else None) or []
        attack = case["bucket"] == "attacks"
        if attack and len(tools) > 1 and case["id"] not in GUARDBENCH_NAMED:
            raise UnlabelledCaseError(f"guardbench {case['id']}: which of its tools is poisoned?")
        expected = case.get("expected")
        action = expected.get("action") if isinstance(expected, dict) else None
        for tool in tools:
            poisoned = attack and (len(tools) == 1 or tool["name"] == GUARDBENCH_NAMED[case["id"]])
            rows.append(
                OodRow(
                    id=f"val-guardbench-{len(rows):04d}",
                    source="guardbench",
                    server_name=case["id"],
                    tool=_tool(tool["name"], tool.get("description"), tool.get("inputSchema")),
                    poisoned=poisoned,
                    provenance={
                        "url": "https://github.com/getmcpm/mcp-guardbench",
                        "commit": commit,
                        "path": path.relative_to(root).as_posix(),
                        "rule": f"guardbench bucket {case['bucket']}"
                        + (" (the case's named tool)" if attack and len(tools) > 1 else ""),
                        "expected_action": action,
                        "indicator_only": bool(poisoned and action == "warn"),
                    },
                )
            )
    return rows


# --------------------------------------------------------------------- Python (AST only)


def _annotation_type(node: ast.expr | None) -> str:
    if node is None:
        return "string"
    text = ast.unparse(node).split("[", 1)[0].split(".")[-1]
    return _PY_TYPES.get(text, "string")


def _signature_schema(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    args = [a for a in fn.args.args if a.arg not in {"self", "ctx", "context"}]
    defaults = len(fn.args.defaults)
    required = [a.arg for a in args[: len(args) - defaults]] if defaults else [a.arg for a in args]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {a.arg: {"type": _annotation_type(a.annotation)} for a in args},
    }
    if required:
        schema["required"] = required
    return schema


def _tool_name(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for decorator in fn.decorator_list:
        if "tool" not in ast.unparse(decorator):
            continue
        if isinstance(decorator, ast.Call):
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value)
        return fn.name
    return None


def python_tools(path: Path) -> list[dict[str, Any]]:
    """Every tool a file declares: `@<x>.tool` functions and literal `types.Tool(...)` calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            name = _tool_name(node)
            if name is not None:
                out.append(_tool(name, ast.get_docstring(node) or "", _signature_schema(node)))
        elif isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "Tool":
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
            if "name" in kwargs:
                out.append(
                    _tool(kwargs["name"], kwargs.get("description"), kwargs.get("inputSchema"))
                )
    return out


def mcpsecbench_files(root: Path) -> list[Path]:
    return sorted((root / "code").glob("*.py"))


def mcpsecbench_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    files = mcpsecbench_files(root)
    verify_clone(root, files, commit, sha256)
    rows: list[OodRow] = []
    for path in files:
        for tool in python_tools(path):
            poisoned = (path.stem, tool["name"]) in MCPSECBENCH_POISONED
            rows.append(
                OodRow(
                    id=f"val-mcpsecbench-{len(rows):04d}",
                    source="mcpsecbench",
                    server_name=path.stem,
                    tool=tool,
                    poisoned=poisoned,
                    provenance={
                        "url": "https://github.com/AIS2Lab/MCPSecBench",
                        "commit": commit,
                        "path": path.relative_to(root).as_posix(),
                        "rule": "description-level PoC in maliciousadd.py"
                        if poisoned
                        else "no description-level attack",
                    },
                )
            )
    return rows


def when_attack_files(root: Path) -> list[Path]:
    return sorted((root / "tool").rglob("*.py"))


def when_attack_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    files = when_attack_files(root)
    verify_clone(root, files, commit, sha256)
    rows: list[OodRow] = []
    for path in files:
        category = path.relative_to(root / "tool").parts[0]
        surface = category.split("-")[1]  # metadata / logic / output
        named = WHEN_METADATA_POISONED.get(category, set())
        for tool in python_tools(path):
            poisoned = category in WHEN_METADATA_POISONED and (
                named is None or tool["name"] in named
            )
            rows.append(
                OodRow(
                    id=f"val-when-attack-{len(rows):04d}",
                    source="when_attack",
                    server_name=f"{category}/{path.stem}",
                    tool=tool,
                    poisoned=poisoned,
                    provenance={
                        "url": "https://github.com/zhao-weibo/when-mcp-servers-attack",
                        "commit": commit,
                        "path": path.relative_to(root).as_posix(),
                        "rule": f"README of {category}",
                        "attack_surface": surface,
                        "borderline": category == "tool-metadata-impersonation",
                    },
                )
            )
    return rows


# --------------------------------------------------------------------------- collection

_READERS = {
    "scannerbench": (scannerbench_rows, scannerbench_files),
    "guardbench": (guardbench_rows, guardbench_files),
    "mcpsecbench": (mcpsecbench_rows, mcpsecbench_files),
    "when_attack": (when_attack_rows, when_attack_files),
}


def load_pins(path: Path = POINTERS) -> dict[str, Pin]:
    return {k: Pin(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _mtp_path(pin: Pin) -> Path:
    from bench.collect.external import fetch_mtp

    return Path(pin.local_dir) if pin.local_dir else fetch_mtp()


def collect_rows(pins: dict[str, Pin]) -> list[OodRow]:
    by_source: dict[str, list[OodRow]] = {}
    for name, (reader, _) in _READERS.items():
        pin = pins[name]
        for row in reader(Path(pin.local_dir), commit=pin.commit, sha256=pin.sha256):
            by_source.setdefault(row.source, []).append(row)
    by_source["mtp"] = mtp_rows(
        _mtp_path(pins["mtp"]), revision=pins["mtp"].commit, sha256=pins["mtp"].sha256
    )
    return [r for s in SOURCES for r in by_source.get(s, [])]


def pin_hashes(pins: dict[str, Pin]) -> dict[str, str]:
    """The content hash of every local source as it is now (to write the pointer file)."""
    out = {
        name: content_hash(Path(pins[name].local_dir), files(Path(pins[name].local_dir)))
        for name, (_, files) in _READERS.items()
    }
    out["mtp"] = hashlib.sha256(_mtp_path(pins["mtp"]).read_bytes()).hexdigest()
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.collect.val_ood")
    parser.add_argument("--out", type=Path, default=OUT)
    ns = parser.parse_args(argv)
    rows = collect_rows(load_pins())
    write_rows(ns.out / ROWS_FILE, rows)
    counts = Counter(r.source for r in rows)
    poisoned = Counter(r.source for r in rows if r.poisoned)
    print(json.dumps({s: {"rows": counts[s], "poisoned": poisoned[s]} for s in SOURCES}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
