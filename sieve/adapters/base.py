"""Shared adapter interface and errors (PRD §10.1).

Every scanner adapter satisfies `ScannerAdapter`: `name` and `version` are plain attributes (not
methods) so a caller can join findings to a pinned version without invoking anything. `scan` runs
the underlying binary offline against already-collected records; `normalise` maps the scanner's
native output to `ScannerFinding`s and must raise `AdapterSchemaError` on an unrecognised
top-level shape so a schema drift in the scanner fails loudly instead of silently under-counting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from sieve.types import ScannerName

if TYPE_CHECKING:
    from sieve.types import ScannerFinding, ToolRecord


class AdapterSchemaError(Exception):
    """Raised when a scanner's native output does not match the shape the adapter expects."""


# Names below are fixed by the lane brief/PRD §10 exactly as written; not renamed to satisfy
# ruff's N818 "*Error" convention.
class AdapterVersionUnpinned(Exception):  # noqa: N818
    """Raised when `bench/versions.lock` has no pinned version line for this adapter's scanner."""


class AdapterCacheMiss(Exception):  # noqa: N818
    """Raised by a caching adapter under `SIEVE_OFFLINE=1` when no cached result exists."""


class SnykCapExceeded(Exception):  # noqa: N818
    """Raised before running Snyk when the persisted invocation counter is at the hard cap."""


class ScannerAdapter(Protocol):
    name: str
    version: str

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        """Offline mode only. Must never start an MCP server."""
        ...

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        """Map native output to ScannerFinding.

        Raise AdapterSchemaError on unknown top-level fields.
        """
        ...


def get_adapter(name: ScannerName) -> ScannerAdapter:
    """Construct the adapter for `name` with its default configuration.

    Only scanners with a real binary-backed adapter class are resolvable here: `cisco_yara`,
    `cisco_llm`, `snyk_agent_scan`. `sarif_in` and `static_json` are pure ingest/parse helpers
    (free functions in their modules) rather than something that runs a scanner, so they are not
    constructed through this factory. Branches are added one at a time, in later tasks of this
    lane, as each adapter class lands — never referencing a module that does not exist yet, so
    `mypy` stays clean at every commit.
    """
    raise ValueError(f"{name!r} has no get_adapter factory; use its module functions directly")
