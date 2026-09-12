from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz")
def readyz() -> dict[str, bool | str]:
    return {"ok": True, "db": "stub", "model": "stub"}
