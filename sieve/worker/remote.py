"""Enumerate an untrusted remote MCP server, in the sandbox container only (SC-1, PRD §13.6).

`docker-compose.yml` runs this image read-only, with every capability dropped, on its own
network, behind `SIEVE_ALLOW_REMOTE_MCP`. Nothing here ever runs in the API process.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx2

from sieve.config import settings
from sieve.types import ToolDefinition
from sieve.worker.ssrf import ResolvedTarget, SsrfBlockedError, check_url
from sieve.worker.ssrf_client import DEADLINE_S, MAX_BYTES, build_client

log = logging.getLogger("sieve.worker.remote")

REMOTE_DISABLED = "remote scanning disabled"
NOT_SANDBOXED = "remote scanning is only permitted inside the sandbox container"

# Set only by the `sandbox-worker` service in docker-compose.yml. It is deliberately read from
# the environment rather than from `sieve.config.Settings`: `Settings` reads `.env`, and the
# whole point of this second gate is that it cannot be turned on by the same `.env` file every
# other service also loads (SC-1).
SANDBOX_ENV_VAR = "SIEVE_SANDBOXED"


class RemoteDisabledError(RuntimeError):
    """`SIEVE_ALLOW_REMOTE_MCP` is false, which is the default everywhere but the sandbox."""


class NotSandboxedError(RuntimeError):
    """The flag is on but this process is not the sandbox container (SC-1)."""


def in_sandbox() -> bool:
    return os.environ.get(SANDBOX_ENV_VAR) == "1"


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
    if not in_sandbox():
        # Two independent gates: a compromised or careless `.env` can flip the first one for
        # every service, but only the sandbox service sets the second.
        raise NotSandboxedError(NOT_SANDBOXED)
    # Resolution is awaited, not blocking: an attacker-named host used to stall the whole
    # worker loop inside `getaddrinfo` (security review 11.1, finding F6).
    target: ResolvedTarget = await check_url(url)
    log.info("remote.fetch", extra={"host": target.host, "family": target.family})

    client = http_client or build_client(target, timeout_s=timeout_s, max_bytes=max_bytes)
    try:
        async with asyncio.timeout(timeout_s):
            return await _list_tools(url, client)
    finally:
        if http_client is None:
            await client.aclose()


def main() -> None:  # pragma: no cover -- process entry point, exercised via `test_sandbox`
    """`Dockerfile.sandbox` runs `python -m sieve.worker.remote`.

    The sandbox is not a different worker, it is the same queue loop in a container that is
    allowed to make outbound connections; running the loop here keeps one dispatch table and
    one recovery story rather than two.
    """
    from sieve.worker.main import main as run_worker

    if not in_sandbox():
        log.warning("sandbox.flag_missing", extra={"var": SANDBOX_ENV_VAR})
    log.info("sandbox.started", extra={"allow_remote_mcp": settings.allow_remote_mcp})
    run_worker()


__all__ = [
    "NOT_SANDBOXED",
    "REMOTE_DISABLED",
    "NotSandboxedError",
    "RemoteDisabledError",
    "SsrfBlockedError",
    "fetch_tools",
    "in_sandbox",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    main()
