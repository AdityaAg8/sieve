"""Both transports the entry point offers: stdio end to end, HTTP down to the SDK call."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

from sieve.mcp_server.__main__ import main, parse_args

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = [sys.executable, "-m", "sieve.mcp_server", "--transport", "stdio"]


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.transport == "stdio"
    assert args.port == 8765
    assert args.api_url is None


def test_http_transport_runs_streamable_http_on_the_requested_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(self: MCPServer, transport: str = "stdio", **kwargs: Any) -> None:
        calls.append((transport, kwargs))

    monkeypatch.setattr(MCPServer, "run", record)
    main(["--transport", "http", "--port", "9123"])

    assert calls == [("streamable-http", {"port": 9123})]


def test_stdio_transport_runs_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(self: MCPServer, transport: str = "stdio", **kwargs: Any) -> None:
        calls.append((transport, kwargs))

    monkeypatch.setattr(MCPServer, "run", record)
    main([])

    assert calls == [("stdio", {})]


async def test_python_m_sieve_mcp_server_answers_tools_list_over_stdio() -> None:
    """The real entry point, in a real subprocess, over a real stdio transport."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sieve.mcp_server", "--transport", "stdio"],
        cwd=REPO_ROOT,
    )
    async with Client(params) as client:
        listed = await client.list_tools()

    assert {t.name for t in listed.tools} == {
        "triage_tool_definition",
        "scan_tools_list",
        "explain_verdict",
    }


def _stdio_exchange(requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Speak raw JSON-RPC to the entry point, without the SDK client in the way."""
    wanted = {r["id"] for r in requests if "id" in r}
    answers: dict[int, dict[str, Any]] = {}
    # S603: a constant argument list run through `sys.executable`, no shell and no user input.
    # `with` closes all three pipes and reaps the child, so no stray ResourceWarning escapes.
    with subprocess.Popen(  # noqa: S603
        ENTRY_POINT,
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            assert process.stdin is not None and process.stdout is not None
            for request in requests:
                process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while wanted - set(answers):
                line = process.stdout.readline()
                if not line:
                    break
                message = json.loads(line)
                if message.get("id") in wanted:
                    answers[message["id"]] = message
        finally:
            process.stdin.close()
            process.terminate()
    return answers


def test_stdio_advertises_a_static_tool_list_on_the_classic_handshake() -> None:
    """`initialize` at 2025-11-25 reports `tools.listChanged: false` -- Sieve's list is static.

    A client that negotiates 2026-07-28 instead sees `true`: at that revision the SDK derives
    the flag from the `subscriptions/listen` endpoint it always serves, so it is the SDK's
    answer rather than Sieve's. Sieve never sends a tools-changed notification either way.
    """
    answers = _stdio_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "sieve-tests", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )

    assert answers[1]["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert answers[1]["result"]["serverInfo"]["name"] == "sieve"
    assert [t["name"] for t in answers[2]["result"]["tools"]] == [
        "triage_tool_definition",
        "scan_tools_list",
        "explain_verdict",
    ]
