"""Everything about a remote MCP URL that can be decided without a resolver (PRD §13.6).

Split out of `sieve/worker/ssrf.py` so the *syntactic* half can run at the API boundary while
the half that needs DNS stays where SC-1 puts it: inside the sandbox. The two used to be one
function, which meant an unparseable or non-`https` URL was accepted with a `202`, queued, and
only refused minutes later inside the sandbox -- as a dead job rather than a 422 (fix round 2,
I3). Nothing here resolves a name, opens a socket, or touches the network in any way.
"""

from __future__ import annotations

from urllib.parse import urlsplit

REQUIRED_SCHEME = "https"
REQUIRED_PORT = 443


class UrlRejectedError(ValueError):
    """The URL is not one Sieve will ever fetch, for a reason visible in the text alone."""


def validated_host(url: str) -> str:
    """The host of a URL Sieve is willing to resolve, or raise `UrlRejectedError`.

    Four refusals, in the order a reader would check them: the scheme, in-URL credentials, a
    missing host, and the port. Credentials are refused rather than stripped -- a URL carrying
    one is a mistake worth telling the submitter about, and quietly fetching without it would
    also quietly store the credential (SC-5).
    """
    parts = urlsplit(url)
    if parts.scheme != REQUIRED_SCHEME:
        raise UrlRejectedError(f"only {REQUIRED_SCHEME}:// is allowed, got {parts.scheme!r}")
    if parts.username or parts.password:
        raise UrlRejectedError("credentials in the URL are not allowed")
    host = parts.hostname
    if not host:
        raise UrlRejectedError("the URL carries no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlRejectedError("the URL carries an unusable port") from exc
    if port not in (None, REQUIRED_PORT):
        raise UrlRejectedError(f"only port {REQUIRED_PORT} is allowed, got {port}")
    return host


__all__ = ["REQUIRED_PORT", "REQUIRED_SCHEME", "UrlRejectedError", "validated_host"]
