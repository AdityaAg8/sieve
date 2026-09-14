"""The SSE progress stream: one event per tool, in order, with `Last-Event-ID` replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api import sse
from sieve.api.sse import parse_last_event_id
from sieve.worker import scan_job
from sieve.worker.main import run_once

TOOLS = json.loads(Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8"))


def parse_sse(body: str) -> list[tuple[str, str, str]]:
    """`(id, event, data)` per message. sse-starlette separates fields with CRLF."""
    out: list[tuple[str, str, str]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        if "event" in fields:
            out.append((fields.get("id", ""), fields["event"], fields.get("data", "")))
    return out


async def completed_scan(api: AsyncClient, sessions: async_sessionmaker[AsyncSession]) -> str:
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": TOOLS}
    )
    scan_id: str = created.json()["id"]
    await run_once(sessions, {"scan": scan_job.run})
    return scan_id


async def read_events(api: AsyncClient, url: str, headers: dict[str, str] | None = None) -> str:
    async with api.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        return "".join([chunk async for chunk in response.aiter_text()])


async def test_the_stream_replays_the_whole_scan_in_order(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan_id = await completed_scan(api, sessions)
    events = parse_sse(await read_events(api, f"/api/v1/scans/{scan_id}/events"))

    kinds = [e[1] for e in events]
    assert kinds[0] == "scan.started"
    assert kinds[-1] == "scan.completed"
    assert kinds.count("tool.verdict") == 27
    assert kinds == ["scan.started", *["tool.verdict"] * 27, "scan.completed"]

    ids = [int(e[0]) for e in events]
    assert ids == sorted(ids), "ids must increase so Last-Event-ID means something"

    verdict = json.loads(events[1][2])
    assert verdict["tool"]["name"]
    assert verdict["result"]["predicted_class"] in {"benign", "imperative_benign", "poisoned"}

    completed = json.loads(events[-1][2])
    assert completed["tool_count"] == 27


async def test_a_reconnect_replays_only_what_was_missed(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan_id = await completed_scan(api, sessions)
    url = f"/api/v1/scans/{scan_id}/events"
    everything = parse_sse(await read_events(api, url))
    midpoint = everything[5][0]

    resumed = parse_sse(await read_events(api, url, headers={"Last-Event-ID": midpoint}))
    assert [e[0] for e in resumed] == [e[0] for e in everything[6:]]
    assert resumed[-1][1] == "scan.completed"


async def test_a_failed_scan_terminates_the_stream(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans",
        json={"source_type": "remote_url", "remote_url": "https://example.invalid/mcp"},
    )
    scan_id = created.json()["id"]
    await run_once(sessions, {"scan": scan_job.run})
    events = parse_sse(await read_events(api, f"/api/v1/scans/{scan_id}/events"))
    assert [e[1] for e in events] == ["scan.started", "scan.failed"]
    assert "remote scanning disabled" in json.loads(events[-1][2])["error"]


async def test_the_stream_404s_for_an_unknown_scan(api: AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await api.get(f"/api/v1/scans/{missing}/events")).status_code == 404


async def test_concurrent_streams_are_capped_and_the_slots_come_back(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security review 11.1, finding F4 (OWASP API4, CWE-770).

    Reads are unlimited by design, but a live SSE stream is not a read: it holds a *dedicated*
    psycopg connection for `LISTEN scan_events` for as long as the client keeps it open, outside
    the SQLAlchemy pool and outside every per-IP quota. One scan id -- the demo's, which is on
    the landing page -- was therefore enough to open connections until Postgres refused new ones
    and the whole API stopped answering, from one host, at no cost.
    """
    monkeypatch.setattr(sse, "MAX_CONCURRENT_STREAMS", 2)
    scan_id = await completed_scan(api, sessions)
    url = f"/api/v1/scans/{scan_id}/events"

    held = [sse.acquire_slot() for _ in range(2)]
    try:
        assert sse.live_streams() == 2
        refused = await api.get(url)
        assert refused.status_code == 503
        assert refused.headers["Retry-After"]
    finally:
        for slot in held:
            slot.release()

    assert sse.live_streams() == 0, "a released slot is available again"
    # And a stream that ran to completion gives its own slot back, not just a hand-made one.
    assert parse_sse(await read_events(api, url))[-1][1] == "scan.completed"
    assert sse.live_streams() == 0


async def test_a_disconnected_stream_gives_its_slot_back(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The interesting leak is the abandoned stream, not the finished one."""
    scan_id = await completed_scan(api, sessions)
    async with api.stream("GET", f"/api/v1/scans/{scan_id}/events") as response:
        assert response.status_code == 200
        async for _chunk in response.aiter_text():
            break  # walk away mid-stream, as a closed browser tab does
    assert sse.live_streams() == 0


def test_a_hostile_last_event_id_is_ignored_not_fatal() -> None:
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("") is None
    assert parse_last_event_id("'; DROP TABLE scans--") is None
    assert parse_last_event_id("-3") is None
    assert parse_last_event_id("42") == 42
