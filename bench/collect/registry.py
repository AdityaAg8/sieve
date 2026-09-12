"""Discovery against the official MCP registry (discovery only — PRD SC-6).

The registry is the *index*; it is never the source of corpus text. It tells us which
servers exist, where their source lives and which package publishes them; the tool
definitions themselves come from `static_py`, `static_ts` or `sandbox_capture`.

Registry shape verified against `https://registry.modelcontextprotocol.io/v0.1/servers`
on 2026-09-12 (see `tests/bench/data/registry-page.json`): the list endpoint moved from
`/v0/servers` to `/v0.1/servers` and the entry is `{"server": {...}, "_meta": {...}}`.
Entries carry no SPDX licence, so `license` starts as `None` and is filled from the
repository's own LICENSE via the GitHub API.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

BASE_URL = "https://registry.modelcontextprotocol.io"
LIST_PATH = "/v0.1/servers"
PAGE_SIZE = 100
PAGE_DELAY_S = 0.2

PackageRegistry = Literal["npm", "pypi", "docker"]
_REGISTRY_TYPES: dict[str, PackageRegistry] = {
    "npm": "npm",
    "pypi": "pypi",
    "oci": "docker",
    "docker": "docker",
}

# PRD SC-6: MPL-2.0 is *excluded* (file-level copyleft), unlike the draft brief's list.
# Task 1.9's publish gate asserts no MPL-2.0 record survives, so it never enters here.
PERMISSIVE_LICENCES: frozenset[str] = frozenset(
    {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}
)


class ServerCandidate(BaseModel):
    name: str
    repo_url: str | None
    package: str | None
    package_registry: PackageRegistry | None
    license: str | None


def _candidate(entry: dict[str, Any]) -> ServerCandidate:
    server = entry.get("server", entry)
    repo = server.get("repository") or {}
    packages = server.get("packages") or []
    package: str | None = None
    package_registry: PackageRegistry | None = None
    for pkg in packages:
        registry_type = str(pkg.get("registryType") or pkg.get("registry_type") or "").lower()
        if registry_type in _REGISTRY_TYPES:
            package = pkg.get("identifier")
            package_registry = _REGISTRY_TYPES[registry_type]
            break
    return ServerCandidate(
        name=server.get("name", ""),
        repo_url=repo.get("url"),
        package=package,
        package_registry=package_registry,
        license=server.get("license"),
    )


def discover(limit: int = 500, *, client: httpx.Client | None = None) -> list[ServerCandidate]:
    """Page through the registry list endpoint until `limit` candidates are collected."""
    owned = client is None
    http = client or httpx.Client(base_url=BASE_URL, timeout=30.0)
    out: list[ServerCandidate] = []
    cursor: str | None = None
    try:
        while True:
            params: dict[str, Any] = {"limit": min(PAGE_SIZE, limit)}
            if cursor:
                params["cursor"] = cursor
            response = http.get(f"{BASE_URL}{LIST_PATH}", params=params)
            response.raise_for_status()
            page = response.json()
            servers = page.get("servers") or []
            out.extend(_candidate(entry) for entry in servers)
            if len(out) >= limit:
                return out[:limit]
            cursor = (page.get("metadata") or {}).get("nextCursor")
            if not cursor or not servers:
                return out
            time.sleep(PAGE_DELAY_S)
    finally:
        if owned:
            http.close()


def filter_permissive(cands: list[ServerCandidate]) -> list[ServerCandidate]:
    """Keep only candidates whose SPDX id is on the permissive allowlist (PRD SC-6)."""
    return [c for c in cands if c.license in PERMISSIVE_LICENCES]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.collect.registry",
        description="Discover MCP server candidates from the official registry (discovery only).",
    )
    parser.add_argument("--limit", type=int, default=500, help="maximum candidates to collect")
    parser.add_argument(
        "--permissive-only",
        action="store_true",
        help="keep only candidates whose SPDX licence is on the permissive allowlist",
    )
    args = parser.parse_args(argv)
    cands = discover(limit=args.limit)
    if args.permissive_only:
        cands = filter_permissive(cands)
    print(json.dumps([c.model_dump() for c in cands], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
