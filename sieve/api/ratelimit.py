"""Postgres-backed rate limits (PRD §13.2).

In Postgres, not in the process: the demo runs more than one process, a restart must not
reset a quota, and an in-process token bucket gives an attacker a free quota per worker.

`ip_hash = SHA-256(SIEVE_IP_HASH_SALT + client_ip)`. The salt matters: IPv4 is a 32-bit
space, so an unsalted digest is reversible by enumeration in seconds. The raw IP is never
stored and never logged (SC-5).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.config import settings

# bucket -> (limit, window_seconds)
WINDOWS: dict[str, tuple[int, int]] = {
    "triage_min": (60, 60),
    "scans_min": (10, 60),
    "scans_day": (settings.daily_scans_per_ip, 86_400),
}

UNKNOWN_IP = "unknown"

_UPSERT = """
INSERT INTO rate_limits (ip_hash, bucket, window_start, count)
VALUES (
    :ip_hash,
    :bucket,
    to_timestamp(floor(extract(epoch from now()) / :window) * :window),
    1
)
ON CONFLICT (ip_hash, bucket, window_start)
DO UPDATE SET count = rate_limits.count + 1
RETURNING count, extract(epoch from rate_limits.window_start) AS started
"""


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_s: int
    limit: int
    remaining: int


def ip_hash(ip: str) -> str:
    return hashlib.sha256((settings.ip_hash_salt + ip).encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    """`X-Forwarded-For`'s left-most entry, and only behind a proxy we chose to trust."""
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client is not None else UNKNOWN_IP


async def hit(session: AsyncSession, hashed_ip: str, bucket: str) -> RateDecision:
    """Count one request against a bucket. One UPSERT, so concurrency cannot double-spend."""
    limit, window = WINDOWS[bucket]
    row = (
        await session.execute(
            text(_UPSERT), {"ip_hash": hashed_ip, "bucket": bucket, "window": window}
        )
    ).one()
    # Commit immediately: a limit that rolls back with the request it refused is not a limit.
    await session.commit()

    # `row.count` is the RETURNING column, not `tuple.count`; index it to say so.
    count = int(row[0])
    elapsed = datetime.now(UTC).timestamp() - float(row[1])
    retry_after = max(1, math.ceil(window - elapsed))
    return RateDecision(
        allowed=count <= limit,
        retry_after_s=retry_after,
        limit=limit,
        remaining=max(0, limit - count),
    )


async def enforce(request: Request, session: AsyncSession, *buckets: str) -> None:
    """Raise 429 with `Retry-After` on the first exhausted bucket."""
    if getattr(request.app.state, "stub", False):
        return  # the fixture-backed app has no database to count in
    hashed = ip_hash(client_ip(request))
    for bucket in buckets:
        decision = await hit(session, hashed, bucket)
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded for {bucket}",
                headers={
                    "Retry-After": str(decision.retry_after_s),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
