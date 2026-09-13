"""The sandbox's HTTP policy and the remote enumeration entry point. All offline."""

from __future__ import annotations

import socket
from collections.abc import Sequence
from typing import Any

import httpx2
import pytest

from sieve.types import ToolDefinition
from sieve.worker import remote
from sieve.worker.ssrf import ResolvedTarget, SsrfBlockedError
from sieve.worker.ssrf_client import (
    GuardTransport,
    PinnedBackend,
    RedirectBlockedError,
    ResponseTooLargeError,
    build_client,
)

TARGET = ResolvedTarget(host="target.example", ip="93.184.216.34", port=443, family=socket.AF_INET)
URL = "https://target.example/mcp"


@pytest.fixture(autouse=True)
def remote_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both gates open: the feature flag *and* the sandbox marker (see `test_sandbox.py`)."""
    monkeypatch.setattr(remote.settings, "allow_remote_mcp", True)
    monkeypatch.setenv(remote.SANDBOX_ENV_VAR, "1")


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, port: Any, *args: Any, **kw: Any) -> Sequence[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def mock_transport(handler: Any) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


# --- request policy ---------------------------------------------------------------------


async def test_the_host_header_is_the_name_while_the_socket_goes_to_the_ip() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    client = build_client(TARGET, inner=mock_transport(handler))
    async with client:
        await client.get(URL)

    assert seen[0].headers["host"] == "target.example"
    assert str(seen[0].url) == URL


async def test_the_pinned_backend_dials_the_literal_not_the_name() -> None:
    backend = PinnedBackend(TARGET.ip)
    dialled: list[tuple[str, int]] = []

    async def fake_super(self: Any, host: str, port: int, **kw: Any) -> Any:
        dialled.append((host, port))
        return object()

    original = type(backend).__mro__[1].connect_tcp
    try:
        type(backend).__mro__[1].connect_tcp = fake_super  # type: ignore[assignment]
        await backend.connect_tcp("target.example", 443)
    finally:
        type(backend).__mro__[1].connect_tcp = original  # type: ignore[assignment]

    assert dialled == [(TARGET.ip, 443)], "no second resolution, so no rebinding window"
    assert backend.dialled == [("target.example", 443)]


@pytest.mark.parametrize(
    "header", ["Authorization", "Cookie", "Proxy-Authorization", "Proxy-Connection"]
)
async def test_credential_and_proxy_headers_never_reach_the_server(header: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client = build_client(TARGET, inner=mock_transport(handler))
    async with client:
        await client.get(URL, headers={header: "secret-value"})

    assert header.lower() not in {k.lower() for k in seen[0].headers}
    assert "secret-value" not in str(dict(seen[0].headers))


async def test_a_client_level_auth_header_is_also_stripped() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client = build_client(TARGET, inner=mock_transport(handler))
    client.headers["Authorization"] = "Bearer nope"
    async with client:
        await client.get(URL)
    assert "authorization" not in {k.lower() for k in seen[0].headers}


# --- response policy --------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_any_redirect_is_a_hard_error(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers={"location": "https://elsewhere.example/"})

    client = build_client(TARGET, inner=mock_transport(handler))
    async with client:
        with pytest.raises(RedirectBlockedError):
            await client.get(URL)


async def test_the_client_never_follows_redirects_by_configuration() -> None:
    client = build_client(TARGET, inner=mock_transport(lambda r: httpx2.Response(200)))
    assert client.follow_redirects is False


async def test_a_body_past_the_cap_aborts_mid_stream() -> None:
    chunks = [b"x" * 100 for _ in range(50)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=iter(chunks))

    client = build_client(TARGET, inner=mock_transport(handler), max_bytes=250)
    read = 0
    async with client:
        with pytest.raises(ResponseTooLargeError):
            async with client.stream("GET", URL) as response:
                async for chunk in response.aiter_raw():
                    read += len(chunk)

    assert read < sum(len(c) for c in chunks), "the body must not be fully read before raising"


async def test_the_capped_stream_closes_the_transport_early() -> None:
    closed: list[bool] = []

    class Recording(httpx2.AsyncByteStream):
        async def __aiter__(self) -> Any:
            for _ in range(50):
                yield b"y" * 100

        async def aclose(self) -> None:
            closed.append(True)

    class Inner(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, stream=Recording())

    guard = GuardTransport(Inner(), max_bytes=250)
    response = await guard.handle_async_request(httpx2.Request("GET", URL))
    with pytest.raises(ResponseTooLargeError):
        async for _ in response.stream:  # type: ignore[union-attr]
            pass
    assert closed == [True], "the transport is closed at the cap, not after the body"


async def test_a_body_inside_the_cap_is_returned_whole() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"z" * 100)

    client = build_client(TARGET, inner=mock_transport(handler), max_bytes=1000)
    async with client:
        assert len((await client.get(URL)).content) == 100


# --- fetch_tools ------------------------------------------------------------------------


async def test_fetch_tools_refuses_to_run_while_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote.settings, "allow_remote_mcp", False)
    with pytest.raises(remote.RemoteDisabledError, match=remote.REMOTE_DISABLED):
        await remote.fetch_tools(URL)


async def test_fetch_tools_validates_before_it_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    def private(host: str, port: Any, *args: Any, **kw: Any) -> Sequence[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", private)
    connected: list[str] = []
    monkeypatch.setattr(
        remote,
        "_list_tools",
        lambda url, client: connected.append(url),  # type: ignore[arg-type]
    )
    with pytest.raises(SsrfBlockedError):
        await remote.fetch_tools(URL)
    assert connected == [], "the guard runs before anything opens a socket"


async def test_fetch_tools_returns_tool_definitions(
    public_dns: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list_tools(url: str, client: httpx2.AsyncClient) -> list[ToolDefinition]:
        captured["url"] = url
        captured["client"] = client
        return [
            ToolDefinition(
                name="remote_tool",
                title=None,
                description="A tool on an untrusted server.",
                inputSchema={"type": "object", "properties": {}},
                outputSchema=None,
                annotations=None,
            )
        ]

    monkeypatch.setattr(remote, "_list_tools", fake_list_tools)
    tools = await remote.fetch_tools(URL, timeout_s=5)

    assert [t.name for t in tools] == ["remote_tool"]
    assert captured["url"] == URL
    assert isinstance(captured["client"]._transport_for_url(httpx2.URL(URL)), GuardTransport)


async def test_fetch_tools_enforces_a_total_deadline(
    public_dns: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def slow(url: str, client: httpx2.AsyncClient) -> list[ToolDefinition]:
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(remote, "_list_tools", slow)
    with pytest.raises(TimeoutError):
        await remote.fetch_tools(URL, timeout_s=1)
