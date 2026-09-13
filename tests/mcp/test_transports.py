"""Both transports the entry point offers: stdio end to end, HTTP down to the SDK call.

Every test here is hermetic with respect to `SIEVE_MODEL_ID` and `ANTHROPIC_API_KEY`. The two
that drive `main()` in-process replace `build_server` so the entry point never reaches
`runtime_deps()` -> `get_classifier()`; the two that spawn a subprocess pin the child's
environment. Without that, a box whose `SIEVE_MODEL_ID` points at a real artefact would load an
ONNX session these tests have no use for, and an uncalibrated one would raise
`ThresholdsMissing` from a test that is not about the classifier at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

import sieve.mcp_server.__main__ as entry_point
from sieve.mcp_server.__main__ import main, parse_args
from sieve.mcp_server.server import build_server
from sieve.pipeline import PipelineDeps
from sieve.protocols import FakeClassifier, FakeJudge

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = [sys.executable, "-m", "sieve.mcp_server", "--transport", "stdio"]

#: The child's environment, pinned to the shared fakes. `ANTHROPIC_API_KEY` is dropped rather
#: than blanked so `Settings` sees it as unset and `get_judge` picks `FakeJudge`.
CHILD_ENV = {
    **{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
    "SIEVE_MODEL_ID": "fixture",
    "PYTHONUTF8": "1",
}


@pytest.fixture
def fake_deps_entry_point(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Make `main()` build a server on the shared fakes, and record what it was asked for."""
    seen: list[dict[str, Any]] = []

    def build(**kwargs: Any) -> MCPServer:
        seen.append(kwargs)
        deps = PipelineDeps(classifier=FakeClassifier(), judge=FakeJudge())
        return build_server(deps=deps, **kwargs)

    monkeypatch.setattr(entry_point, "build_server", build)
    return seen


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.transport == "stdio"
    assert args.port == 8765
    assert args.api_url is None


def test_http_transport_runs_streamable_http_on_the_requested_port(
    monkeypatch: pytest.MonkeyPatch, fake_deps_entry_point: list[dict[str, Any]]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(self: MCPServer, transport: str = "stdio", **kwargs: Any) -> None:
        calls.append((transport, kwargs))

    monkeypatch.setattr(MCPServer, "run", record)
    main(["--transport", "http", "--port", "9123", "--api-url", "http://api.test"])

    assert calls == [("streamable-http", {"port": 9123})]
    assert fake_deps_entry_point == [{"api_url": "http://api.test"}]


def test_stdio_transport_runs_stdio(
    monkeypatch: pytest.MonkeyPatch, fake_deps_entry_point: list[dict[str, Any]]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(self: MCPServer, transport: str = "stdio", **kwargs: Any) -> None:
        calls.append((transport, kwargs))

    monkeypatch.setattr(MCPServer, "run", record)
    main([])

    assert calls == [("stdio", {})]
    assert fake_deps_entry_point == [{"api_url": None}]


async def test_python_m_sieve_mcp_server_answers_tools_list_over_stdio() -> None:
    """The real entry point, in a real subprocess, over a real stdio transport."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sieve.mcp_server", "--transport", "stdio"],
        cwd=REPO_ROOT,
        env=CHILD_ENV,
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
        env=CHILD_ENV,
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
