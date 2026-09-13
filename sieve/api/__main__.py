"""`python -m sieve.api` -- the supported way to run the API.

It exists so the event loop is chosen through uvicorn's own `loop=` option rather than by a
policy uvicorn no longer consults (see `sieve.api.loop`). `make serve` uses it.
"""

from __future__ import annotations

import argparse

import uvicorn

from sieve.api.loop import LOOP_FACTORY_PATH

APP = "sieve.api.main:app"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m sieve.api", description="Run the Sieve API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="reload on source changes (dev)")
    parser.add_argument("--log-level", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # The app is passed as an import string, not an object, so `--reload` keeps working.
    uvicorn.run(
        APP,
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=LOOP_FACTORY_PATH,
        log_level=args.log_level,
    )


if __name__ == "__main__":  # pragma: no cover -- process entry point
    main()
