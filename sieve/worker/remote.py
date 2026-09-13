"""Enumerate an untrusted remote MCP server, in the sandbox container only (SC-1, PRD §13.6).

`docker-compose.yml` runs this image read-only, with every capability dropped, on its own
network, behind `SIEVE_ALLOW_REMOTE_MCP`. Nothing here ever runs in the API process.
"""

from __future__ import annotations

import asyncio
import logging

import httpx2

from sieve.config import settings
from sieve.types import ToolDefinition
from sieve.worker.ssrf import ResolvedTarget, SsrfBlockedError, check_url
from sieve.worker.ssrf_client import DEADLINE_S, MAX_BYTES, build_client

log = logging.getLogger("sieve.worker.remote")

REMOTE_DISABLED = "remote scanning disabled"


class RemoteDisabledError(RuntimeError):
    """`SIEVE_ALLOW_REMOTE_MCP` is false, which is the default everywhere but the sandbox."""


async def _list_tools(url: str, client: httpx2.AsyncClient) -> list[ToolDefinition]:
    # Imported here so the API image never pays for the MCP client at startup.
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    transport = streamable_http_client(url, http_client=client)
    async with Client(transport) as session:
        result = await session.list_tools()
    return [ToolDefinition.model_validate(tool.model_dump(by_alias=True)) for tool in result.tools]


async def fetch_tools(
    url: str,
    *,
    timeout_s: int = DEADLINE_S,
    max_bytes: int = MAX_BYTES,
    http_client: httpx2.AsyncClient | None = None,
) -> list[ToolDefinition]:
    """Validate, pin, connect, enumerate. Everything it returns is untrusted data (SC-7)."""
    if not settings.allow_remote_mcp:
        raise RemoteDisabledError(REMOTE_DISABLED)
    target: ResolvedTarget = check_url(url)
    log.info("remote.fetch", extra={"host": target.host, "family": target.family})

    client = http_client or build_client(target, timeout_s=timeout_s, max_bytes=max_bytes)
    try:
        async with asyncio.timeout(timeout_s):
            return await _list_tools(url, client)
    finally:
        if http_client is None:
            await client.aclose()


__all__ = [
    "REMOTE_DISABLED",
    "RemoteDisabledError",
    "SsrfBlockedError",
    "fetch_tools",
]
