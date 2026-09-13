"""`cisco_llm` adapter: Cisco's LLM analyzer as a second baseline (PRD §10.2, P1 polish tier).

There is no LLM provider key configured in this environment (`--llm-api-key` /
`MCP_SCANNER_LLM_API_KEY` unset), so this adapter cannot be exercised end-to-end against the real
binary here. It mirrors `cisco_yara`'s shape exactly (`raw_to_by_tool` reshapes the scanner's
`--raw` array for the `llm_analyzer` block into the same `{tool_name: {"findings": [...]}}`
document, strict Pydantic schemas raise `AdapterSchemaError` on drift) so that whichever
environment does have a key can drop it in unchanged. What is new here is the cache: an LLM call
costs money and is nondeterministic, so `scan()` runs the binary **at most once per corpus
version** and persists the raw output keyed by a hash of the serialised records. A cache miss
under `SIEVE_OFFLINE=1` raises `AdapterCacheMiss` instead of ever shelling out — offline mode must
never depend on a network call succeeding.

The committed fixture `tests/adapters/data/cisco-llm-sample.json` is authored (not captured): it
mirrors `fixtures/scanner/cisco-yara-sample-27.raw.json`'s real `--raw` shape for a `yara_analyzer`
block, with the block renamed to `llm_analyzer` and its two flagged tools' `mcp_taxonomies`
descriptions rewritten to read like a language-model's prose judgement (no imperative keyword to
regex-match) rather than a YARA rule hit — see `lessons.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # noqa: S404 (subprocess is the adapter's whole job; every call is targeted)
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from sieve.adapters.base import AdapterCacheMiss, AdapterSchemaError, AdapterVersionUnpinned
from sieve.serialise import serialise
from sieve.types import ScannerFinding, ToolRecord

SCANNER_NAME = "cisco_llm"


class _Finding(BaseModel):
    """One rule hit for one tool, in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: str | None = None
    message: str | None = None


class _ToolEntry(BaseModel):
    """The value for one tool name in the by_tool document."""

    model_config = ConfigDict(extra="forbid")

    findings: list[_Finding]


def raw_to_by_tool(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Reshape the scanner's `--raw` array (`llm_analyzer` block) into the `by_tool` document.

    Identical transform to `cisco_yara.raw_to_by_tool`: `threat_names[i]` pairs positionally with
    `mcp_taxonomies[i]`; `rule_id` is the threat name lower-cased with spaces turned to
    underscores, `severity` is the analyzer's severity lower-cased, `message` is that taxonomy
    entry's `description`.
    """
    by_tool: dict[str, Any] = {}
    for entry in raw:
        tool_name = entry["tool_name"]
        llm = entry["findings"]["llm_analyzer"]
        threat_names: list[str] = llm.get("threat_names", [])
        taxonomies: list[dict[str, Any]] = llm.get("mcp_taxonomies", [])
        severity = llm.get("severity")
        findings = []
        for i, threat_name in enumerate(threat_names):
            rule_id = threat_name.lower().replace(" ", "_")
            message = taxonomies[i]["description"] if i < len(taxonomies) else None
            findings.append(
                {
                    "rule_id": rule_id,
                    "severity": severity.lower() if isinstance(severity, str) else None,
                    "message": message,
                }
            )
        by_tool[tool_name] = {"findings": findings}
    return by_tool


def _read_pinned_version(lockfile: Path, scanner: str) -> str:
    """Read `<scanner>=<version>` from the lockfile, stripping any trailing `# comment`."""
    if not lockfile.exists():
        raise AdapterVersionUnpinned(f"lockfile not found: {lockfile}")
    prefix = f"{scanner}="
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].split("#", 1)[0].strip()
    raise AdapterVersionUnpinned(f"no {prefix!r} line in {lockfile}")


def _records_hash(records: list[ToolRecord]) -> str:
    """Stable hash of the serialised records: the cache key for one corpus version."""
    texts = [serialise(r) for r in records]
    digest = hashlib.sha256(json.dumps(texts, sort_keys=False).encode("utf-8"))
    return digest.hexdigest()[:16]


class CiscoLlmAdapter:
    """Runs Cisco's LLM analyzer once per corpus version and caches the raw output."""

    name = SCANNER_NAME

    def __init__(
        self,
        binary: str = "mcp-scanner",
        cache_dir: Path = Path("bench/data/raw/cisco_llm"),
        lockfile: Path = Path("bench/versions.lock"),
    ) -> None:
        self.binary = binary
        self.cache_dir = cache_dir
        self.version = _read_pinned_version(lockfile, SCANNER_NAME)

    def _cache_path(self, records: list[ToolRecord]) -> Path:
        return self.cache_dir / f"{_records_hash(records)}.json"

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        """Offline-safe: one real invocation per distinct corpus version, then cached forever."""
        cache_path = self._cache_path(records)
        if cache_path.exists():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            return self.normalise(raw_to_by_tool(raw))

        if os.environ.get("SIEVE_OFFLINE") == "1":
            raise AdapterCacheMiss(
                f"no cached cisco_llm output at {cache_path} and SIEVE_OFFLINE=1: "
                "the LLM analyzer never runs unless a cache already exists"
            )

        tools = [r.tool.model_dump(mode="json", exclude_none=True) for r in records]
        fd, path_str = tempfile.mkstemp(suffix=".json")
        tools_path = Path(path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"tools": tools}, fh)
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(  # noqa: S603 (fixed argv below; no shell, no untrusted input)
                [self.binary, "--analyzers", "llm", "--raw", "static", "--tools", str(tools_path)],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
        finally:
            tools_path.unlink(missing_ok=True)

        raw = json.loads(result.stdout) if result.stdout.strip() else []
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
        return self.normalise(raw_to_by_tool(raw))

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        """Map a `by_tool` document to `ScannerFinding`s, one per (tool, rule) pair."""
        try:
            parsed = {name: _ToolEntry(**entry) for name, entry in raw.items()}
        except (ValidationError, TypeError) as exc:
            raise AdapterSchemaError(f"unrecognised cisco_llm by_tool schema: {exc}") from exc

        out: list[ScannerFinding] = []
        for tool_name, entry in parsed.items():
            for finding in entry.findings:
                out.append(
                    ScannerFinding(
                        scanner=SCANNER_NAME,
                        scanner_version=self.version,
                        rule_id=finding.rule_id,
                        severity=finding.severity,
                        message=finding.message,
                        raw={"tool_name": tool_name},
                    )
                )
        return out
