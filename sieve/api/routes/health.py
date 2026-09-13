from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from sieve.api.deps import get_classifier
from sieve.db.session import session_factory

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, bool | str]:
    """Readiness checks the database and the model load (PRD §13.1)."""
    if getattr(request.app.state, "stub", False):
        return {"ok": True, "db": "stub", "model": "stub"}
    try:
        async with session_factory()() as session:
            await session.execute(text("SELECT 1"))
        db = "ok"
    except Exception as exc:  # noqa: BLE001 -- readiness reports, it does not raise
        db = f"error: {type(exc).__name__}"
    try:
        model = get_classifier().model_version
    except Exception as exc:  # noqa: BLE001
        model = f"error: {type(exc).__name__}"
    return {"ok": db == "ok" and not model.startswith("error"), "db": db, "model": model}
