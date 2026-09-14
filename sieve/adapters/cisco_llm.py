"""`cisco_llm` adapter: Cisco's LLM analyzer as a second baseline (PRD §10.2, P1 polish tier).

There is no LLM provider key configured in this environment (`--llm-api-key` /
`MCP_SCANNER_LLM_API_KEY` unset), so this adapter cannot be exercised end-to-end against the real
binary here. It mirrors `cisco_yara`'s shape exactly — both share the by_tool reshape, strict
schema parsing and version-pinning via `sieve.adapters._scanner_common` (review round 1, finding
4), differing only in which analyzer block they read (`llm_analyzer` here) — so that whichever
environment does have a key can drop it in unchanged. What is new here is the cache: an LLM call
costs money and is nondeterministic, so `scan()` runs the binary **at most once per corpus
version** and persists the raw output keyed by a hash of the serialised records. A cache miss
under `SIEVE_OFFLINE=1` raises `AdapterCacheMiss` instead of ever shelling out — offline mode must
never depend on a network call succeeding.

The committed fixture `tests/adapters/data/cisco-llm-sample.AUTHORED.json` is authored, not
captured: it mirrors `fixtures/scanner/cisco-yara-sample-27.raw.json`'s real `--raw` shape for a
`yara_analyzer` block, with the block renamed to `llm_analyzer` and its two flagged tools'
`mcp_taxonomies` descriptions rewritten to read like a language-model's prose judgement (no
imperative keyword to regex-match) rather than a YARA rule hit. The
`.AUTHORED.` marker in the filename is deliberate (review round 1, minor): nothing here was ever
produced by a real LLM analyzer run.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # noqa: S404 (subprocess is the adapter's whole job; every call is targeted)
import tempfile
from pathlib import Path
from typing import Any

from sieve.adapters import _scanner_common as common
from sieve.adapters.base import AdapterCacheMiss
from sieve.serialise import serialise
from sieve.types import ScannerFinding, ScannerName, ToolRecord

SCANNER_NAME: ScannerName = "cisco_llm"


def raw_to_by_tool(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Reshape the scanner's `--raw` array (`llm_analyzer` block) into the `by_tool` document."""
    return common.raw_to_by_tool(raw, analyzer_key="llm_analyzer")


def _records_hash(records: list[ToolRecord]) -> str:
    """Stable hash of the serialised records: the cache key for one corpus version."""
    texts = [serialise(r) for r in records]
    digest = hashlib.sha256(json.dumps(texts, sort_keys=False).encode("utf-8"))
    return digest.hexdigest()[:16]


class CiscoLlmAdapter:
    """Runs Cisco's LLM analyzer once per corpus version and caches the raw output."""

    name: str = SCANNER_NAME

    def __init__(
        self,
        binary: str = "mcp-scanner",
        cache_dir: Path = Path("bench/data/raw/cisco_llm"),
        lockfile: Path = Path("bench/versions.lock"),
    ) -> None:
        self.binary = binary
        self.cache_dir = cache_dir
        self.version = common.read_pinned_version(lockfile, SCANNER_NAME)

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

        # SC-5: redact credential-shaped strings before anything is written to disk for the
        # scanner to read (the temp input file) or persisted in the on-disk cache's input hash.
        tools = common.redacted_tool_dicts(records)
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
        parsed = common.parse_by_tool(raw, schema_name=SCANNER_NAME)
        return common.to_scanner_findings(parsed, SCANNER_NAME, self.version)
