"""SSRF and DNS-rebinding guard for the sandboxed remote worker (PRD §13.6).

Two ideas do the work:

1. **Resolve everything, reject on any.** A name that answers with one public A record and
   one private AAAA record is hostile; checking only the first answer is the classic bug.
2. **Pin the address.** The caller connects to the single validated IP literal with `Host`
   and TLS SNI set to the original name, so there is no second resolution between the check
   and the connect -- and therefore no rebinding window.

IPv4-mapped (`::ffff:0:0/96`) and NAT64 (`64:ff9b::/96`) addresses are unwrapped and
re-checked against the IPv4 list, because `::ffff:10.0.0.1` is a private address in a v6 hat.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from pydantic import BaseModel

REQUIRED_SCHEME = "https"
REQUIRED_PORT = 443

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
    address = ipaddress.ip_address(raw)
    if address in METADATA_ADDRESSES:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in net for net in BLOCKED_V4)
    if any(address in net for net in BLOCKED_V6):
        return True
    inner = _unwrapped(address)
    return inner is not None and is_blocked(str(inner))


def _resolve(host: str) -> list[tuple[int, str]]:
    try:
        answers = socket.getaddrinfo(host, REQUIRED_PORT, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SsrfBlockedError(f"could not resolve {host!r}") from exc
    out: list[tuple[int, str]] = [
        (int(family), str(sockaddr[0])) for family, _t, _p, _c, sockaddr in answers
    ]
    if not out:
        raise SsrfBlockedError(f"could not resolve {host!r}")
    return out


def check_url(url: str) -> ResolvedTarget:
    """Validate a remote MCP URL and pin it to one address, or raise `SsrfBlockedError`."""
    parts = urlsplit(url)
    if parts.scheme != REQUIRED_SCHEME:
        raise SsrfBlockedError(f"only {REQUIRED_SCHEME}:// is allowed, got {parts.scheme!r}")
    if parts.username or parts.password:
        raise SsrfBlockedError("credentials in the URL are not allowed")
    host = parts.hostname
    if not host:
        raise SsrfBlockedError("the URL carries no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise SsrfBlockedError("the URL carries an unusable port") from exc
    if port not in (None, REQUIRED_PORT):
        raise SsrfBlockedError(f"only port {REQUIRED_PORT} is allowed, got {port}")

    answers = _resolve(host)
    for _family, address in answers:
        if is_blocked(address):
            # Say which address, never the whole answer set: one blocked answer is enough.
            raise SsrfBlockedError(f"{host!r} resolves to a blocked address ({address})")

    family, ip = answers[0]
    return ResolvedTarget(host=host, ip=ip, port=REQUIRED_PORT, family=family)
