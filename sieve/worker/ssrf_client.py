"""The HTTP client the sandbox uses: address-pinned, header-stripped, redirect-refusing, capped.

Split out of `remote.py` so the network policy can be tested on its own, with an
`httpx2.MockTransport` standing in for the wire.
"""

from __future__ import annotations

import ssl
import typing

import httpcore2
import httpx2

from sieve.worker.ssrf import ResolvedTarget

# Headers the sandbox must never forward to an untrusted server, whatever the caller set.
STRIPPED_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
STRIPPED_PREFIXES = ("proxy-",)

MAX_BYTES = 1_000_000
DEADLINE_S = 10


class RedirectBlockedError(RuntimeError):
    """A 3xx. Following it would mean a second, unvalidated resolution (PRD §13.6)."""


class ResponseTooLargeError(RuntimeError):
    """The body went past the cap. Raised mid-stream, not after buffering."""


class PinnedBackend(httpcore2.AnyIOBackend):
    """Connect to the validated literal, whatever name the URL carries.

    The URL keeps the original host, so `Host` and TLS SNI are the name the certificate is
    issued for; only the TCP destination is overridden. That is the whole rebinding defence:
    the address checked is the address dialled, with no second `getaddrinfo` in between.
    """

    def __init__(self, ip: str) -> None:
        super().__init__()
        self.ip = ip
        self.dialled: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        self.dialled.append((host, port))
        return await super().connect_tcp(
            self.ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedTransport(httpx2.AsyncHTTPTransport):
    """`AsyncHTTPTransport` over a pool that dials the pinned literal.

    `AsyncHTTPTransport.__init__` builds an `AsyncConnectionPool` of its own and takes no
    `network_backend`, so calling it and then reassigning `self._pool` left the first pool
    unreferenced and unclosed -- one more object with a live `aclose` nobody ever awaits, in a
    suite that runs under `-W error` where a GC-timed `ResourceWarning` surfaces as an exception
    inside whichever test happens to be running (fix round 2, M6). The pool is therefore built
    once, here. That is the complete contract: `self._pool` is the only attribute the base class
    touches -- `handle_async_request`, `aclose` and the async context manager all read it and
    nothing else.
    """

    def __init__(self, target: ResolvedTarget, *, verify: ssl.SSLContext | bool = True) -> None:
        self.backend = PinnedBackend(target.ip)
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(verify=verify),
            network_backend=self.backend,
            http1=True,
            http2=False,
        )


class _CappedStream(httpx2.AsyncByteStream):
    """Counts bytes as they arrive and aborts the response the moment the cap is passed."""

    def __init__(self, inner: typing.Any, max_bytes: int) -> None:
        self._inner = inner
        self._max = max_bytes
        self.closed = False

    async def _chunks(self) -> typing.AsyncIterator[bytes]:
        # A transport may hand back either kind of stream (`MockTransport` returns a sync one
        # for sync content); the cap has to apply to both.
        if hasattr(self._inner, "__aiter__"):
            async for chunk in self._inner:
                yield chunk
        else:
            for chunk in self._inner:
                yield chunk

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        seen = 0
        async for chunk in self._chunks():
            seen += len(chunk)
            if seen > self._max:
                await self.aclose()
                raise ResponseTooLargeError(
                    f"response exceeded {self._max} bytes; aborted after {seen}"
                )
            yield chunk

    async def aclose(self) -> None:
        self.closed = True
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()


class GuardTransport(httpx2.AsyncBaseTransport):
    """Applies the §13.6 request and response policy to every call the MCP client makes."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, *, max_bytes: int = MAX_BYTES) -> None:
        self.inner = inner
        self.max_bytes = max_bytes
        self.sent: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        for name in list(request.headers):
            lowered = name.lower()
            if lowered in STRIPPED_HEADERS or lowered.startswith(STRIPPED_PREFIXES):
                del request.headers[name]
        self.sent.append(request)

        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise RedirectBlockedError(
                f"refusing a {response.status_code}: a redirect is a second, unvalidated host"
            )
        return httpx2.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CappedStream(response.stream, self.max_bytes),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def build_client(
    target: ResolvedTarget,
    *,
    timeout_s: int = DEADLINE_S,
    max_bytes: int = MAX_BYTES,
    inner: httpx2.AsyncBaseTransport | None = None,
) -> httpx2.AsyncClient:
    """The only client the sandbox is allowed to open."""
    transport = GuardTransport(inner or PinnedTransport(target), max_bytes=max_bytes)
    return httpx2.AsyncClient(
        transport=transport,
        follow_redirects=False,  # belt and braces: GuardTransport also refuses every 3xx
        timeout=httpx2.Timeout(timeout_s),
        trust_env=False,  # no ambient proxy, no ambient CA override
    )
