"""`POST /scans` -> queue -> worker -> `GET /scans/{id}`, against a real Postgres."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.worker import scan_job
from sieve.worker.main import run_once

TOOLS = json.loads(Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8"))
SCANNER = json.loads(Path("fixtures/scanner/cisco-yara-sample-27.json").read_text(encoding="utf-8"))
DISPATCH = {"scan": scan_job.run}


async def tick(sessions: async_sessionmaker[AsyncSession]) -> bool:
    return await run_once(sessions, DISPATCH)


async def test_a_posted_scan_is_queued_then_completed_by_the_worker(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": TOOLS}
    )
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "queued"
    assert body["tool_count"] == 27

    assert await tick(sessions) is True

    summary = (await api.get(f"/api/v1/scans/{body['id']}")).json()
    assert summary["status"] == "complete"
    assert summary["confirmed"] + summary["needs_review"] + summary["dismissed"] == 27
    assert summary["model_version"] is not None
    assert summary["error"] is None


async def test_the_posted_tools_are_the_ones_triaged(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The Phase 0 stub ignored the body and replayed a fixture; the real store must not."""
    one_tool: dict[str, Any] = {
        "servers": {"solo": ["only_tool"]},
        "tools": [
            {
                "name": "only_tool",
                "description": "Reads a value.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    }
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": one_tool}
    )
    scan_id = created.json()["id"]
    assert created.json()["tool_count"] == 1
    await tick(sessions)

    page = (await api.get(f"/api/v1/scans/{scan_id}/findings")).json()
    assert page["total"] == 1
    assert [f["tool"]["name"] for f in page["items"]] == ["only_tool"]
    assert page["items"][0]["server_name"] == "solo"


async def test_credentials_in_a_description_are_redacted_before_storage(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    leaky: dict[str, Any] = {
        "servers": {"srv": ["leaky"]},
        "tools": [
            {
                "name": "leaky",
                "description": "Authenticate with AKIAIOSFODNN7EXAMPLE first.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    }
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": leaky}
    )
    await tick(sessions)
    page = (await api.get(f"/api/v1/scans/{created.json()['id']}/findings")).json()
    stored = page["items"][0]["tool"]["description"]
    assert "AKIA" not in stored
    assert "[REDACTED:aws_access_key]" in stored


async def test_scanner_findings_travel_with_the_tools(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans",
        json={
            "source_type": "scanner_report",
            "tools_json": TOOLS,
            "scanner_report": SCANNER,
            "scanner": "cisco_yara",
        },
    )
    assert created.status_code == 202
    assert created.json()["scanner_flagged"] > 0
    await tick(sessions)

    page = (await api.get(f"/api/v1/scans/{created.json()['id']}/findings")).json()
    flagged = [f for f in page["items"] if f["result"]["scanner_findings"]]
    assert flagged, "the original scanner verdict is never dropped"
    assert flagged[0]["result"]["scanner_findings"][0]["scanner"] == "cisco_yara"


async def test_idempotency_key_reuse_returns_the_same_scan(api: AsyncClient) -> None:
    body = {"source_type": "tools_json", "tools_json": TOOLS}
    first = await api.post("/api/v1/scans", json=body, headers={"Idempotency-Key": "abc-123"})
    second = await api.post("/api/v1/scans", json=body, headers={"Idempotency-Key": "abc-123"})
    assert first.status_code == 202
    assert second.status_code == 200, "a replay is not a second scan"
    assert first.json()["id"] == second.json()["id"]


async def test_a_different_idempotency_key_creates_a_new_scan(api: AsyncClient) -> None:
    body = {"source_type": "tools_json", "tools_json": TOOLS}
    first = await api.post("/api/v1/scans", json=body, headers={"Idempotency-Key": "k1"})
    second = await api.post("/api/v1/scans", json=body, headers={"Idempotency-Key": "k2"})
    assert first.json()["id"] != second.json()["id"]


async def test_too_many_tools_is_413(api: AsyncClient) -> None:
    tools = {
        "servers": {"big": [f"tool_{i}" for i in range(501)]},
        "tools": [
            {
                "name": f"tool_{i}",
                "description": "A tool.",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for i in range(501)
        ],
    }
    response = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": tools}
    )
    assert response.status_code == 413


async def test_a_source_type_without_its_payload_is_422(api: AsyncClient) -> None:
    assert (await api.post("/api/v1/scans", json={"source_type": "tools_json"})).status_code == 422
    assert (await api.post("/api/v1/scans", json={"source_type": "remote_url"})).status_code == 422


async def test_an_unknown_scan_is_404_everywhere(api: AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await api.get(f"/api/v1/scans/{missing}")).status_code == 404
    assert (await api.get(f"/api/v1/scans/{missing}/findings")).status_code == 404
    assert (await api.get(f"/api/v1/scans/{missing}/sarif")).status_code == 404
    assert (await api.get("/api/v1/scans/not-a-uuid")).status_code == 404


async def test_findings_paginate_and_filter(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": TOOLS}
    )
    scan_id = created.json()["id"]
    await tick(sessions)

    first = (await api.get(f"/api/v1/scans/{scan_id}/findings?limit=10")).json()
    assert len(first["items"]) == 10
    assert first["total"] == 27
    assert first["next_cursor"] == "10"

    last = (await api.get(f"/api/v1/scans/{scan_id}/findings?limit=10&cursor=20")).json()
    assert len(last["items"]) == 7
    assert last["next_cursor"] is None

    dismissed = (await api.get(f"/api/v1/scans/{scan_id}/findings?verdict=dismissed")).json()
    assert dismissed["total"] < 27
    assert {f["result"]["verdict"] for f in dismissed["items"]} == {"dismissed"}

    by_class = (await api.get(f"/api/v1/scans/{scan_id}/findings?predicted_class=poisoned")).json()
    assert {f["result"]["predicted_class"] for f in by_class["items"]} == {"poisoned"}

    by_stage = (await api.get(f"/api/v1/scans/{scan_id}/findings?stage=classifier")).json()
    assert {f["result"]["stage"] for f in by_stage["items"]} == {"classifier"}


async def test_a_malformed_cursor_is_400_not_500(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans", json={"source_type": "tools_json", "tools_json": TOOLS}
    )
    scan_id = created.json()["id"]
    assert (
        await api.get(f"/api/v1/scans/{scan_id}/findings?cursor=not-a-number")
    ).status_code == 400
    assert (await api.get(f"/api/v1/scans/{scan_id}/findings?cursor=-1")).status_code == 400


async def test_a_remote_scan_fails_closed_while_remote_mcp_is_disabled(
    api: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post(
        "/api/v1/scans",
        json={"source_type": "remote_url", "remote_url": "https://example.invalid/mcp"},
    )
    assert created.status_code == 202
    await tick(sessions)
    summary = (await api.get(f"/api/v1/scans/{created.json()['id']}")).json()
    assert summary["status"] == "failed"
    assert "remote scanning disabled" in summary["error"]


async def test_readyz_reports_the_database_and_the_model(api: AsyncClient) -> None:
    body = (await api.get("/api/v1/readyz")).json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert body["model"]


async def test_models_reports_the_artefact_thresholds(api: AsyncClient) -> None:
    info = (await api.get("/api/v1/models")).json()
    assert 0.0 < info["t_low"] < info["t_high"] <= 1.0
    assert info["model_version"]
