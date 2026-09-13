from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sieve.api.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from sieve.api.routes import bench, feedback, findings, health, models, scans, triage
from sieve.config import settings
from sieve.db.session import use_selector_event_loop_on_windows
from sieve.logging import configure_logging


def create_app(*, stub: bool = False) -> FastAPI:
    # Before uvicorn builds its loop: psycopg cannot run async on Windows' ProactorEventLoop.
    use_selector_event_loop_on_windows()
    configure_logging(replace=False)
    app = FastAPI(
        title="Sieve API",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.state.stub = stub
    for r in (scans, triage, findings, feedback, bench, models, health):
        app.include_router(r.router, prefix="/api/v1")
    return app


app = create_app()
