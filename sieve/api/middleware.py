"""Security headers and the 1 MB body cap (PRD §13.2).

The body cap is pure ASGI rather than `BaseHTTPMiddleware` because the point is to stop
reading, not to read it all and then complain: a chunked upload with no `Content-Length`
would otherwise be buffered in full before anyone checked its size.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1_000_000

# `default-src 'none'` is right for an API: it serves JSON and SSE, never a document.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-site",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                for name, value in SECURITY_HEADERS.items():
                    if name.lower().encode() not in present:
                        headers.append((name.lower().encode(), value.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _too_large(self, send: Send) -> None:
        body = json.dumps({"detail": f"request body exceeds {self.max_bytes} bytes"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                announced = int(declared)
            except ValueError:
                announced = 0  # a malformed header is the body counter's problem, not a 500
            if announced > self.max_bytes:
                await self._too_large(send)
                return

        seen = 0
        over = False

        async def limited_receive() -> Message:
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    over = True
                    # Truncate and end the stream: the handler sees a short body and fails
                    # validation, and the wrapper below turns that into the 413 it really is.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        sent_start = False

        async def guarded_send(message: Message) -> None:
            nonlocal sent_start
            if over and not sent_start:
                sent_start = True
                await self._too_large(send)
                return
            if over:
                return
            if message["type"] == "http.response.start":
                sent_start = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


__all__ = [
    "MAX_BODY_BYTES",
    "SECURITY_HEADERS",
    "BodySizeLimitMiddleware",
    "SecurityHeadersMiddleware",
]
