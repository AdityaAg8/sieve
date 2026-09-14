"""Postgres-backed rate limits (PRD §13.2).

In Postgres, not in the process: the demo runs more than one process, a restart must not
reset a quota, and an in-process token bucket gives an attacker a free quota per worker.

`ip_hash = HMAC-SHA256(key=SIEVE_IP_HASH_SALT, message=client_ip)`. The salt matters: IPv4 is
a 32-bit space, so an unsalted digest is reversible by enumeration in seconds. The raw IP is
never stored and never logged (SC-5).

**Why HMAC and not a KDF** (security review 11.1, finding F7; PRD §13.2's residual risk 1).
The input is an IP address, so the message space is 2^32 for IPv4 and, in practice, not much
larger for IPv6. A slow hash cannot fix that: at one hash per microsecond an Argon2 tuned to
100 ms only buys ~5 log2 bits of work against an attacker who wants *one* address confirmed,
while costing that same 100 ms on every request `hit()` serves -- a self-inflicted DoS on the
one code path that exists to prevent DoS. What actually protects the pseudonym is that the
key is secret and per-deployment, and the primitive for "secret key, public message" is a
MAC, not a digest of their concatenation: `sha256(key || message)` is a construction with a
known length-extension weakness and no security proof in this role, and HMAC is the standard
one that has both. The cost is one extra compression block per request.
"""

from __future__ import annotations

import hashlib
import hmac
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
    """A keyed pseudonym for one client address. See the module docstring for why HMAC."""
    return hmac.new(
        settings.ip_hash_salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def client_ip(request: Request) -> str:
    """`X-Forwarded-For`'s **right-most** entry behind a trusted proxy; the socket peer otherwise.

    Right-most, not left-most (security review 11.1 round 1, finding 2). Left-most is the
    internet's claim about who the client is, and the internet is what writes it: every hop
    *appends*, so a caller who sends `X-Forwarded-For: <victim>` has our proxy append its own
    view and produce `<victim>, <caller>`. Reading left-most then hands the attacker back the
    string they chose. On its own that is quota evasion; once `ip_hash` also namespaces
    `Idempotency-Key` (`sieve/api/routes/scans.py`) it is worse, because an attacker who knows a
    victim's address can re-enter the victim's namespace and read their scan — which is exactly
    the BOLA that namespacing was added to close.

    The right-most entry is the only one this deployment can vouch for: it is the one *our*
    trusted proxy wrote. That is correct for **exactly one** appending hop, which is the topology
    `docs/deploy.md` describes — a single Fly app behind Fly's own proxy. Behind two or more
    hops every caller collapses to the inner proxy's address: a quota collapse, never a
    cross-client read, recorded in THREAT_MODEL.md's residual risks.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # A header of nothing but separators and spaces is no answer at all; fall through to
            # the peer rather than pseudonymising everyone who sends one under `""`.
            entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
            if entries:
                return entries[-1]
    client = request.client
    return client.host if client is not None else UNKNOWN_IP


def limits_for(bucket: str) -> tuple[int, int]:
    """`WINDOWS` is built at import; the daily quota is read live so a test (or a redeploy)
    that changes `SIEVE_DAILY_SCANS_PER_IP` is not ignored until the process restarts."""
    limit, window = WINDOWS[bucket]
    if bucket == "scans_day":
        limit = settings.daily_scans_per_ip
    return limit, window


async def hit(session: AsyncSession, hashed_ip: str, bucket: str) -> RateDecision:
    """Count one request against a bucket. One UPSERT, so concurrency cannot double-spend."""
    limit, window = limits_for(bucket)
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
