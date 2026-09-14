"""SSRF and DNS-rebinding guard for the sandboxed remote worker (PRD §13.6).

Two ideas do the work:

1. **Resolve everything, reject on any.** A name that answers with one public A record and
   one private AAAA record is hostile; checking only the first answer is the classic bug.
2. **Pin the address.** The caller connects to the single validated IP literal with `Host`
   and TLS SNI set to the original name, so there is no second resolution between the check
   and the connect -- and therefore no rebinding window.

IPv4-mapped (`::ffff:0:0/96`) and NAT64 (`64:ff9b::/96`) addresses are unwrapped and
re-checked against the IPv4 list, because `::ffff:10.0.0.1` is a private address in a v6 hat.

`check_url` is **async** (security review 11.1, finding F6). `socket.getaddrinfo` is a blocking
C call, and the name being resolved is the attacker's: a resolver that answers slowly, or a
nameserver the attacker controls and simply never answers from, used to stall the worker's
whole event loop -- the heartbeat included -- for as long as it pleased. The 10 s fetch
deadline bounded the request *after* the connection, not the lookup before it. Resolution now
goes through `loop.getaddrinfo`, which runs it in the default executor, under its own
`RESOLVE_TIMEOUT_S`. One caveat worth stating: the executor thread is not killed when the
timeout fires -- nothing in CPython can interrupt a blocking `getaddrinfo` -- so a hostile
resolver can still occupy a pool thread until the OS gives up. What it can no longer do is
occupy the loop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

from pydantic import BaseModel

from sieve.urls import REQUIRED_PORT, UrlRejectedError, validated_host

#: How long the attacker's nameserver gets before the name is refused. Well under the 10 s
#: whole-fetch deadline, because resolution is the first of several steps inside it.
RESOLVE_TIMEOUT_S = 5.0

BLOCKED_V4 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # CGNAT, and Alibaba's metadata address lives inside it
        "192.0.2.0/24",
        "198.18.0.0/15",
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",
        "255.255.255.255/32",
    )
)

BLOCKED_V6 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::/128",
        "::1/128",
        "fc00::/7",  # unique-local, and fd00:ec2::254 lives inside it
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
        "::ffff:0:0/96",  # IPv4-mapped
        "64:ff9b::/96",  # NAT64
        "2001:db8::/32",
    )
)

# Named explicitly as well as by range, so the intent survives a future range edit.
METADATA_ADDRESSES = tuple(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
)

NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
MAPPED_PREFIX = ipaddress.ip_network("::ffff:0:0/96")


class SsrfBlockedError(ValueError):
    """The URL, or an address it resolves to, is not one the sandbox may reach."""


class ResolvedTarget(BaseModel):
    host: str  # the original name, used for Host and SNI
    ip: str  # the single validated literal actually connected to
    port: int  # always 443
    family: int  # AF_INET | AF_INET6


def _unwrapped(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address hiding inside a mapped or NAT64 v6 address, if there is one."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address in MAPPED_PREFIX or address in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_blocked(raw: str) -> bool:
    """True when this literal is in any range §13.6 forbids."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        # A scoped literal (`fe80::1%eth0`) or anything else `ipaddress` will not parse is
        # something we cannot reason about, so it is refused, not passed through.
        raise SsrfBlockedError(f"unparseable address {raw!r}") from exc
    if address in METADATA_ADDRESSES:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in net for net in BLOCKED_V4)
    if any(address in net for net in BLOCKED_V6):
        return True
    inner = _unwrapped(address)
    return inner is not None and is_blocked(str(inner))


def _answers(raw: list[Any], host: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = [
        (int(family), str(sockaddr[0])) for family, _t, _p, _c, sockaddr in raw
    ]
    if not out:
        raise SsrfBlockedError(f"could not resolve {host!r}")
    return out


async def _resolve(host: str, *, timeout_s: float) -> list[tuple[int, str]]:
    """Every A and AAAA answer, resolved off the event loop and under a deadline (F6)."""
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(timeout_s):
            raw = await loop.getaddrinfo(host, REQUIRED_PORT, type=socket.SOCK_STREAM)
    except TimeoutError as exc:
        # A name nobody answers for is refused like any other name we cannot reason about.
        raise SsrfBlockedError(f"resolving {host!r} took longer than {timeout_s}s") from exc
    except OSError as exc:
        raise SsrfBlockedError(f"could not resolve {host!r}") from exc
    return _answers(list(raw), host)


def _validated_host(url: str) -> str:
    """Everything about the URL that can be decided without a resolver.

    The checks themselves live in `sieve/urls.py` so the API can run them at POST time and
    answer 422 instead of queueing a scan that only fails inside the sandbox minutes later
    (fix round 2, I3). They are re-raised as `SsrfBlockedError` here because, from this
    module's callers' point of view, a refusal is a refusal whatever decided it.
    """
    try:
        return validated_host(url)
    except UrlRejectedError as exc:
        raise SsrfBlockedError(str(exc)) from exc


async def check_url(url: str, *, timeout_s: float = RESOLVE_TIMEOUT_S) -> ResolvedTarget:
    """Validate a remote MCP URL and pin it to one address, or raise `SsrfBlockedError`."""
    host = _validated_host(url)
    answers = await _resolve(host, timeout_s=timeout_s)
    for _family, address in answers:
        if is_blocked(address):
            # Say which address, never the whole answer set: one blocked answer is enough.
            raise SsrfBlockedError(f"{host!r} resolves to a blocked address ({address})")

    family, ip = answers[0]
    return ResolvedTarget(host=host, ip=ip, port=REQUIRED_PORT, family=family)
