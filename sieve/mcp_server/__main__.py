"""`python -m sieve.mcp_server --transport stdio|http [--port 8765] [--api-url URL]`.

stdio is the default because that is how a local client (Claude Code, an IDE) starts a server
it owns; `--transport http` serves the same three tools over the spec's streamable-HTTP
transport for a Sieve deployed once and shared.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from sieve.config import settings
from sieve.mcp_server.server import build_server


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m sieve.mcp_server", description="Serve Sieve's triage tools over MCP."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for a client that starts this process; http for streamable HTTP.",
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Port for --transport http (default: 8765)."
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Base URL of a Sieve API; explain_verdict needs it to read stored findings.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    # stdio *is* the protocol channel: a stray print would corrupt the stream, so logging goes
    # to stderr, which is where `logging.basicConfig` already sends it.
    logging.basicConfig(level=settings.log_level)
    server = build_server(api_url=args.api_url)
    if args.transport == "http":
        server.run(transport="streamable-http", port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
