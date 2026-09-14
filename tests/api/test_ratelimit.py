"""Postgres-backed rate limits: they survive a restart and a second process (PRD §13.2)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api.main import DEFAULT_IP_HASH_SALT, create_app
from sieve.api.ratelimit import WINDOWS, client_ip, hit, ip_hash
from sieve.config import settings
from sieve.db.models import RateLimit

TOOLS = json.loads(Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8"))
RECORD = {
    "id": "srv/read_file",
    "server_name": "srv",
    "tool": {
        "name": "read_file",
        "description": "Reads a file.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "sibling_tool_names": [],
}


def test_the_ip_hash_is_salted_and_never_the_raw_ip() -> None:
    """An unsalted hash of a 32-bit space is enumerable in seconds (PRD §13.2).

    Security review 11.1, finding F7: the salt is a *key*, not a per-row salt, so the primitive
    is HMAC-SHA256 keyed by it rather than `sha256(salt || ip)`. The old construction is also
    asserted absent, because "we changed it" is the only part of this a future edit could undo
    by accident.
    """
    hashed = ip_hash("203.0.113.7")
    assert hashed != hashlib.sha256(b"203.0.113.7").hexdigest()
    assert hashed != hashlib.sha256((settings.ip_hash_salt + "203.0.113.7").encode()).hexdigest()
    assert (
        hashed
        == hmac.new(
            settings.ip_hash_salt.encode("utf-8"), b"203.0.113.7", hashlib.sha256
        ).hexdigest()
    )
    assert "203.0.113.7" not in hashed


def test_a_different_salt_gives_a_different_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ip_hash("203.0.113.7")
    monkeypatch.setattr(settings, "ip_hash_salt", "another-deployment")
    assert ip_hash("203.0.113.7") != first


def test_the_shipped_default_salt_is_said_out_loud_at_startup(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key everyone can read from the repository is not a key (finding F7).

    `ip_hash_salt` defaults to a placeholder so a checkout runs, which means a deployment that
    never set it pseudonymises every client IP under a value an attacker already has. It cannot
    be a hard refusal -- `make up` and every test build an app -- so it is a warning that names
    the variable, and this test is what keeps it from being silently deleted.
    """

    def warnings() -> list[str]:
        # The variable name rides in `extra`, which `caplog.text` does not render; the JSON
        # formatter the deployment actually runs does.
        return [
            str(getattr(r, "detail", ""))
            for r in caplog.records
            if r.name == "sieve.api" and r.getMessage() == "ip_hash.default_salt"
        ]

    monkeypatch.setattr(settings, "ip_hash_salt", DEFAULT_IP_HASH_SALT)
    with caplog.at_level(logging.WARNING, logger="sieve.api"):
        create_app()
    assert any("SIEVE_IP_HASH_SALT" in detail for detail in warnings())

    caplog.clear()
    monkeypatch.setattr(settings, "ip_hash_salt", "a-real-per-deployment-key")
    with caplog.at_level(logging.WARNING, logger="sieve.api"):
        create_app()
    assert warnings() == []


class FakeRequest:
    def __init__(self, headers: dict[str, str], peer: str | None) -> None:
        self.headers = headers
        self.client = type("C", (), {"host": peer})() if peer else None


def test_forwarded_for_is_only_trusted_behind_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    request = FakeRequest({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "10.0.0.9")
    monkeypatch.setattr(settings, "trust_proxy", False)
    assert client_ip(request) == "10.0.0.9"  # type: ignore[arg-type]
    monkeypatch.setattr(settings, "trust_proxy", True)
    assert client_ip(request) == "1.2.3.4", "left-most entry only"  # type: ignore[arg-type]


def test_a_missing_peer_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy", False)
    assert client_ip(FakeRequest({}, None)) == "unknown"  # type: ignore[arg-type]


async def test_the_count_survives_a_new_session(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The whole point of Postgres over an in-process bucket."""
    async with sessions() as first:
        for _ in range(3):
            await hit(first, ip_hash("198.51.100.1"), "triage_min")
    async with sessions() as second:
        decision = await hit(second, ip_hash("198.51.100.1"), "triage_min")
    assert decision.remaining == WINDOWS["triage_min"][0] - 4


async def test_concurrent_hits_cannot_both_pass_the_boundary(
    sessions: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    async def once() -> None:
        async with sessions() as s:
            await hit(s, ip_hash("198.51.100.2"), "scans_min")

    await asyncio.gather(*(once() for _ in range(50)))
    count = (
        await session.execute(
            select(RateLimit.count).where(RateLimit.ip_hash == ip_hash("198.51.100.2"))
        )
    ).scalar_one()
    assert count == 50, "the UPSERT is the only counter, and it is atomic"


async def test_the_decision_reports_a_usable_retry_after(session: AsyncSession) -> None:
    decision = await hit(session, ip_hash("198.51.100.3"), "triage_min")
    assert decision.allowed is True
    assert 0 < decision.retry_after_s <= WINDOWS["triage_min"][1]
    assert decision.limit == WINDOWS["triage_min"][0]


async def test_the_61st_triage_in_a_minute_is_429(api: AsyncClient) -> None:
    limit = WINDOWS["triage_min"][0]
    for _ in range(limit):
        assert (await api.post("/api/v1/triage", json=RECORD)).status_code == 200
    over = await api.post("/api/v1/triage", json=RECORD)
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) > 0


async def test_the_11th_scan_in_a_minute_is_429(api: AsyncClient) -> None:
    body = {"source_type": "tools_json", "tools_json": TOOLS}
    limit = WINDOWS["scans_min"][0]
    for _ in range(limit - 1):
        assert (await api.post("/api/v1/scans", json=body)).status_code == 202
    assert (await api.post("/api/v1/scans", json=body)).status_code == 202
    assert (await api.post("/api/v1/scans", json=body)).status_code == 429


async def test_the_daily_quota_holds_across_minutes(
    api: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spread across windows: the minute bucket resets, the day bucket does not."""
    hashed = ip_hash(client_ip(FakeRequest({}, "127.0.0.1")))  # type: ignore[arg-type]
    await session.execute(
        text(
            "INSERT INTO rate_limits (ip_hash, bucket, window_start, count) "
            "VALUES (:h, 'scans_day', "
            "to_timestamp(floor(extract(epoch from now()) / 86400) * 86400), :n)"
        ),
        {"h": hashed, "n": settings.daily_scans_per_ip},
    )
    await session.commit()
    body = {"source_type": "tools_json", "tools_json": TOOLS}
    response = await api.post("/api/v1/scans", json=body)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


async def test_reads_are_not_rate_limited(api: AsyncClient) -> None:
    for _ in range(WINDOWS["scans_min"][0] + 5):
        assert (await api.get("/api/v1/healthz")).status_code == 200
