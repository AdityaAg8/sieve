from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sieve.api.deps import close_judge
from sieve.api.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from sieve.api.routes import bench, feedback, findings, health, models, scans, triage
from sieve.config import settings
from sieve.db.session import use_selector_event_loop_on_windows
from sieve.logging import configure_logging

log = logging.getLogger("sieve.api")

#: The value `sieve.config.Settings` ships so a fresh checkout runs. It is a placeholder, not a
#: key, and `sieve.api.ratelimit.ip_hash` uses the salt as an HMAC key -- so a deployment that
#: never set `SIEVE_IP_HASH_SALT` is pseudonymising client IPs under a value anyone can read out
#: of this repository (security review 11.1, finding F7).
DEFAULT_IP_HASH_SALT = "change-me-per-deployment"  # noqa: S105 -- a placeholder, not a secret


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Shutdown closes the judge's HTTP client rather than leaving it to the collector."""
    yield
    close_judge()


def create_app(*, stub: bool = False) -> FastAPI:
    # Before uvicorn builds its loop: psycopg cannot run async on Windows' ProactorEventLoop.
    use_selector_event_loop_on_windows()
    configure_logging(replace=False)
    if settings.ip_hash_salt == DEFAULT_IP_HASH_SALT:
        # Said out loud rather than refused: `make up`, the fixture app and every test build an
        # app, and a checkout that will not start is not a security control anyone keeps.
        log.warning(
            "ip_hash.default_salt",
            extra={
                "detail": "SIEVE_IP_HASH_SALT is still the shipped placeholder; client IP "
                "pseudonyms are reversible by anyone who can read the repository. Set it to a "
                "random per-deployment value before exposing this API."
            },
        )
    app = FastAPI(
        title="Sieve API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    # Order matters: the last one added is the outermost, and the body cap answers 413 itself
    # without ever reaching the app, so the header middleware has to wrap it.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.stub = stub
    for r in (scans, triage, findings, feedback, bench, models, health):
        app.include_router(r.router, prefix="/api/v1")
    return app


app = create_app()
