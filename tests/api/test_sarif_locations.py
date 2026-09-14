"""What the API is allowed to put in a SARIF `physicalLocation` (fix round 2, M3).

`artifactLocation.uri` is a **file**, and the API never has one. `scans.source_ref` is a
comma-joined list of server names for an `mcp_config` scan and a redacted URL for a
`remote_url` one -- neither is repo-relative, so emitting it made GitHub code scanning either
drop the location or attribute the finding to a path that does not exist in the repository.

The CLI is the surface that *does* know a path (`--output`, `tests/test_sarif.py`), and it
still carries locations. This module is the other half: the API says nothing rather than
something wrong.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.test_scans import tick

CONFIG: dict[str, Any] = {
    "mcpServers": {
        "weather": {
            "command": "npx",
            "tools": [
                {
                    "name": "get_forecast",
                    "description": "Return the forecast for a place.",
                    "inputSchema": {"type": "object"},
                }
            ],
        },
        "docs-index": {"url": "https://docs.example.invalid/mcp"},
    }
}


async def test_an_mcp_config_scan_exports_no_sarif_locations(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans", json={"source_type": "mcp_config", "mcp_config": CONFIG}
    )
    scan_id = created.json()["id"]
    assert created.json()["tool_count"] == 1, "the declared tool is lifted, so there is a result"
    await tick(sessions)

    results = (await api.get(f"/api/v1/scans/{scan_id}/sarif")).json()["runs"][0]["results"]
    assert results, "a SARIF run with no results would prove nothing about locations"
    assert all("locations" not in r for r in results), (
        "the server-name list is not a path, so it is not an artifactLocation"
    )


async def test_the_store_offers_no_path_for_any_scan(
    api: AsyncClient, session: AsyncSession
) -> None:
    """Stated at the source, not only in the exported document.

    `DbStore.source_path` used to return `scans.source_ref` verbatim. It now answers `None`
    unconditionally, because no API source type produces a repo-relative path -- and a future
    one that does answers here rather than in `sieve/sarif.py`.
    """
    from sieve.api.store import DbStore

    created = await api.post(
        "/api/v1/scans", json={"source_type": "mcp_config", "mcp_config": CONFIG}
    )
    assert await DbStore(session).source_path(created.json()["id"]) is None
