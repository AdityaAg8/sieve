"""The SSRF and DNS-rebinding guard (PRD §13.6). One case per blocked range.

Everything here is offline: `getaddrinfo` is monkeypatched, so the tests assert the policy,
not the internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Sequence
from typing import Any

import pytest

from sieve.worker.ssrf import SsrfBlockedError, check_url

# Exactly the list in the lane brief: every range PRD §13.6 names, including the two the
# v2.0 guard missed (IPv4-mapped and NAT64).
BLOCKED = [
    "0.0.0.0",  # noqa: S104 -- a blocked destination, not a bind address
    "10.1.2.3",
    "127.0.0.1",
    "169.254.1.1",
    "169.254.169.254",
    "172.16.5.5",
    "192.168.1.1",
    "100.64.0.1",
    "100.100.100.200",
    "224.0.0.1",
    "255.255.255.255",
    "::1",
    "fc00::1",
    "fe80::1",
    "ff02::1",
    "fd00:ec2::254",
    "::ffff:10.0.0.1",
    "64:ff9b::0a00:0001",
]

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def addrinfo(*addresses: str) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for address in addresses:
        if ":" in address:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)))
    return out


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def install(*addresses: str) -> None:
        def fake(host: str, port: Any, *args: Any, **kw: Any) -> Sequence[tuple[Any, ...]]:
            return addrinfo(*addresses)

        monkeypatch.setattr(socket, "getaddrinfo", fake)

    return install


@pytest.mark.parametrize("address", BLOCKED)
async def test_every_blocked_range_is_blocked(resolver: Any, address: str) -> None:
    resolver(address)
    with pytest.raises(SsrfBlockedError):
        await check_url("https://target.example/mcp")


async def test_a_public_host_resolves_to_a_pinned_target(resolver: Any) -> None:
    resolver(PUBLIC_V4)
    target = await check_url("https://target.example/mcp")
    assert target.host == "target.example", "Host and SNI keep the original name"
    assert target.ip == PUBLIC_V4
    assert target.port == 443
    assert target.family == socket.AF_INET


async def test_a_public_ipv6_host_is_allowed(resolver: Any) -> None:
    resolver(PUBLIC_V6)
    target = await check_url("https://target.example/mcp")
    assert target.ip == PUBLIC_V6
    assert target.family == socket.AF_INET6


async def test_a_private_second_answer_blocks_the_whole_name(resolver: Any) -> None:
    """Checking only the first answer is the bug; a poisoned AAAA must sink the request."""
    resolver(PUBLIC_V4, "fd00::1")
    with pytest.raises(SsrfBlockedError):
        await check_url("https://target.example/mcp")


async def test_a_private_first_answer_blocks_even_with_a_public_second(resolver: Any) -> None:
    resolver("10.0.0.7", PUBLIC_V4)
    with pytest.raises(SsrfBlockedError):
        await check_url("https://target.example/mcp")


async def test_http_is_refused(resolver: Any) -> None:
    resolver(PUBLIC_V4)
    with pytest.raises(SsrfBlockedError, match="https"):
        await check_url("http://target.example/mcp")


async def test_a_non_443_port_is_refused(resolver: Any) -> None:
    resolver(PUBLIC_V4)
    with pytest.raises(SsrfBlockedError, match="443"):
        await check_url("https://target.example:8443/mcp")


async def test_credentials_in_the_url_are_refused(resolver: Any) -> None:
    resolver(PUBLIC_V4)
    with pytest.raises(SsrfBlockedError, match="credential"):
        await check_url("https://user:pw@target.example/mcp")


async def test_a_url_without_a_host_is_refused() -> None:
    with pytest.raises(SsrfBlockedError):
        await check_url("https:///mcp")


async def test_a_name_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kw: Any) -> Sequence[tuple[Any, ...]]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(SsrfBlockedError, match="resolve"):
        await check_url("https://target.example/mcp")


async def test_a_name_that_resolves_to_nothing_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(SsrfBlockedError, match="resolve"):
        await check_url("https://target.example/mcp")


async def test_an_explicit_443_is_fine(resolver: Any) -> None:
    resolver(PUBLIC_V4)
    assert (await check_url("https://target.example:443/mcp")).port == 443


async def test_a_public_ip_literal_is_still_checked(resolver: Any) -> None:
    resolver("127.0.0.1")
    with pytest.raises(SsrfBlockedError):
        await check_url("https://127.0.0.1/mcp")


# ------------------------------------------- the resolver runs off the loop (review 11.1, F6)


def _slow_resolver(
    monkeypatch: pytest.MonkeyPatch, seconds: float, *addresses: str
) -> threading.Event:
    """A nameserver that answers, eventually. `loop.getaddrinfo` calls this in an executor.

    The returned event releases the executor thread early. It has to exist: nothing can
    interrupt a blocking `getaddrinfo`, so a thread left sleeping past its test is one the
    interpreter waits for at shutdown -- the same caveat `sieve/worker/ssrf.py` documents,
    reproduced here as a test cost rather than left to slow the suite down.
    """
    released = threading.Event()

    def fake(host: str, port: Any, *args: Any, **kw: Any) -> Sequence[tuple[Any, ...]]:
        released.wait(seconds)
        return addrinfo(*addresses)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return released


async def test_a_slow_resolver_does_not_stall_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding F6: the host being resolved is the attacker's, so its latency is theirs to pick.

    The guard used to call `socket.getaddrinfo` straight from an `async def`, which stops the
    worker's loop dead -- the heartbeat that keeps this worker's job lease alive included -- for
    however long a hostile nameserver felt like taking. The ticker below is the loop: if it
    advances while the lookup is outstanding, the loop was free.
    """
    _slow_resolver(monkeypatch, 0.4, PUBLIC_V4)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        target = await check_url("https://target.example/mcp")
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker

    assert target.ip == PUBLIC_V4
    assert ticks > 5, f"the loop ran {ticks} times while resolution was outstanding"


async def test_a_resolver_that_never_answers_is_refused_on_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the wait is bounded: the 10 s fetch deadline never covered the lookup before it."""
    released = _slow_resolver(monkeypatch, 30.0, PUBLIC_V4)
    started = time.monotonic()
    try:
        with pytest.raises(SsrfBlockedError, match="longer than"):
            await check_url("https://target.example/mcp", timeout_s=0.1)
        assert time.monotonic() - started < 5, "the deadline is the guard's, not the resolver's"
    finally:
        released.set()  # let the abandoned executor thread go; see `_slow_resolver`
