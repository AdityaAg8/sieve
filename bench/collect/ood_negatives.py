"""Test-OOD negatives (training plan G2, §5.2): Docker's registry and MCPTox's clean tools.

Both become binary eval rows (`OodRow`, `poisoned=False`), reconstructed from pinned upstream
clones and never committed: `bench/data/ood_negatives.pointers.json` holds the URL, the commit
and a content hash for each, and a clone off its pin is refused.

* **Docker** (`docker/mcp-registry`, `servers/*/tools.json`): 755 tools on 28 servers. A tool
  that lists Docker's `arguments` instead of an `inputSchema` gets the equivalent JSON Schema.
* **MCPTox clean** (`response_all.json`, the per-server clean system prompts): 362 tools, unique
  per (server, tool), with the server name hashed exactly as the `real_poisoned` slice hashes it
  (`hash_server_name`, G1), so a clean tool and its poisoned twins share one cluster.

The YARA sidecar maps row id -> flagged. Tools are renamed `t<i>` for the scan so a result maps
back to one row: joining by the real tool name is the F58 bug. Neither source is
`real_poisoned`, and M13 is keyed on that source (`bench.eval_final.m13_rows`), so these
negatives never enter it. Counts here are before the §5.2 cross dedup (G4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.collect.ood_rows import OodRow, write_rows
from bench.collect.real_poisoned import hash_server_name

Runner = Callable[[str, list[str]], str]

SOURCES = ("docker", "mcptox_clean")
POINTERS = Path("bench/data/ood_negatives.pointers.json")
EXTERNAL = Path("bench/data/external")
OUT = Path("bench/data/ood")
ROWS_FILE = "test_ood_negatives.jsonl"
YARA_FILE = "test_ood_negatives.yara.json"
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_CLEAN_TOOL = re.compile(r"Tool: (.+?)\nDescription: (.*?)\nArguments:", re.S)


class PinMismatchError(RuntimeError):
    """The local clone is not at the pinned commit, or its content hash moved."""


@dataclass(frozen=True)
class Pin:
    url: str
    commit: str
    sha256: str
    licence: str
    local_dir: str


def load_pins(path: Path = POINTERS) -> dict[str, Pin]:
    return {k: Pin(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def _head(root: Path) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH, as the A3 clones were made
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def content_hash(root: Path, files: Sequence[Path]) -> str:
    """sha256 over (relative path, file sha256) of every file read, in order."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def verify_clone(root: Path, files: Sequence[Path], commit: str, sha256: str | None) -> None:
    head = _head(root)
    if not head.startswith(commit) and not commit.startswith(head):
        raise PinMismatchError(f"{root} is at {head[:12]}, pinned to {commit[:12]}")
    if sha256 is not None and content_hash(root, files) != sha256:
        raise PinMismatchError(f"{root} content does not match its pinned sha256")


def _schema(tool: dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool.get("inputSchema"), dict):
        return dict(tool["inputSchema"])
    arguments = tool.get("arguments")
    if not arguments:
        return dict(EMPTY_SCHEMA)
    properties: dict[str, Any] = {}
    for arg in arguments:
        prop = {"type": arg.get("type", "string"), "description": arg.get("desc")}
        if "items" in arg:
            prop["items"] = arg["items"]
        properties[arg["name"]] = {k: v for k, v in prop.items() if v is not None}
    required = [a["name"] for a in arguments if not a.get("optional")]
    return {"type": "object", "properties": properties, "required": required}


def docker_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    files = sorted((root / "servers").glob("*/tools.json"))
    verify_clone(root, files, commit, sha256)
    rows: list[OodRow] = []
    for path in files:
        for tool in json.loads(path.read_text(encoding="utf-8")) or []:
            definition = {k: v for k, v in tool.items() if k not in {"arguments", "inputSchema"}}
            definition["inputSchema"] = _schema(tool)
            rows.append(
                OodRow(
                    id=f"ood-docker-{len(rows):04d}",
                    source="docker",
                    server_name=path.parent.name,
                    tool=definition,
                    poisoned=False,
                    provenance={
                        "url": "https://github.com/docker/mcp-registry",
                        "commit": commit,
                        "path": path.relative_to(root).as_posix(),
                        "schema": "inputSchema" if "inputSchema" in tool else "from_arguments",
                    },
                )
            )
    return rows


def mcptox_clean_rows(root: Path, *, commit: str, sha256: str | None = None) -> list[OodRow]:
    path = root / "response_all.json"
    verify_clone(root, [path], commit, sha256)
    rows: list[OodRow] = []
    seen: set[tuple[str, str]] = set()
    servers = json.loads(path.read_text(encoding="utf-8"))["servers"]
    for server, entry in servers.items():
        for match in _CLEAN_TOOL.finditer(entry["clean_system_promot"]):
            name, description = match.group(1), match.group(2).strip()
            if (server, name) in seen:
                continue
            seen.add((server, name))
            rows.append(
                OodRow(
                    id=f"ood-mcptox-clean-{len(rows):04d}",
                    source="mcptox_clean",
                    server_name=hash_server_name(server),
                    tool={"name": name, "description": description, "inputSchema": EMPTY_SCHEMA},
                    poisoned=False,
                    provenance={
                        "url": "https://github.com/zhiqiangwang4/MCPTox-Benchmark",
                        "commit": commit,
                        "path": "response_all.json",
                    },
                )
            )
    return rows


def _run(binary: str, args: list[str]) -> str:
    from bench.analysis.plan_evidence.yara import _run as run

    return run(binary, args)


def yara_sidecar(
    rows: Sequence[OodRow], workdir: Path, *, runner: Runner = _run, batch: int = 2000
) -> dict[str, bool]:
    """Row id -> flagged by Cisco's YARA analyzer (static mode, nothing executed)."""
    workdir.mkdir(parents=True, exist_ok=True)
    flags: dict[str, bool] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        tools = [
            {"name": f"t{i}", "description": r.tool.get("description") or "",
             "inputSchema": r.tool["inputSchema"]}
            for i, r in enumerate(chunk)
        ]  # fmt: skip
        path = workdir / f"ood_scan_{start}.json"
        path.write_text(json.dumps({"tools": tools}, ensure_ascii=False), encoding="utf-8")
        out = runner(
            "mcp-scanner", ["--analyzers", "yara", "--raw", "static", "--tools", str(path)]
        )
        results = {r["tool_name"]: r for r in json.loads(out[out.index("[") :])}
        flags.update({r.id: not results[f"t{i}"]["is_safe"] for i, r in enumerate(chunk)})
    return flags


def collect(
    *,
    docker: Path,
    mcptox: Path,
    out: Path,
    pins: dict[str, str],
    hashes: dict[str, str] | None = None,
    runner: Runner = _run,
    yara: bool = True,
) -> dict[str, int]:
    hashes = hashes or {}
    rows = docker_rows(docker, commit=pins["docker"], sha256=hashes.get("docker"))
    rows += mcptox_clean_rows(
        mcptox, commit=pins["mcptox_clean"], sha256=hashes.get("mcptox_clean")
    )
    assert not {r.source for r in rows} - set(SOURCES)
    write_rows(out / ROWS_FILE, rows)
    if yara:
        flags = yara_sidecar(rows, out / "scan", runner=runner)
        (out / YARA_FILE).write_text(
            json.dumps(flags, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return {s: sum(r.source == s for r in rows) for s in SOURCES}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.collect.ood_negatives")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--no-yara", action="store_true")
    ns = parser.parse_args(argv)
    pins = load_pins()
    counts = collect(
        docker=Path(pins["docker"].local_dir),
        mcptox=Path(pins["mcptox_clean"].local_dir),
        out=ns.out,
        pins={k: p.commit for k, p in pins.items()},
        hashes={k: p.sha256 for k, p in pins.items()},
        yara=not ns.no_yara,
    )
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
