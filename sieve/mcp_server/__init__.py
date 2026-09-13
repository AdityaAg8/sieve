"""Sieve as an MCP server (PRD §16).

Sieve is a triage layer, so this server is a triage service and nothing else. Its tools take
tool definitions as *data* and return verdicts; none of them starts, connects to, installs or
otherwise executes another MCP server (PRD SC-1). Everything inside a submitted definition is
untrusted (SC-7) and is redacted before the judge sees it (SC-5) -- both by virtue of going
through `sieve.pipeline.triage`, the one function every surface calls.
"""

from __future__ import annotations

from sieve.mcp_server.server import build_server

__all__ = ["build_server"]
