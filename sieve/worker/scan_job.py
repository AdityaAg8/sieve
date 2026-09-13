"""The `scan` job: load a scan's tools, triage them, stream verdicts, update the counters.

`triage_many` runs in a worker thread because a real DeBERTa forward pass is CPU-bound and
would otherwise stall the event loop; `on_result` hands each verdict back to the loop, so a
row and an SSE event land per tool rather than all 27 at the end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.api import spend
from sieve.api.deps import get_classifier, get_judge
from sieve.api.store import verdict_row
from sieve.config import settings
from sieve.db.models import Job, Scan, ScanEvent, ScannerFindingRow, Tool, VerdictRow
from sieve.pipeline import PipelineDeps, triage_many
from sieve.redact import redact_tool
from sieve.types import (
    Finding,
    ScannerFinding,
    ToolDefinition,
    ToolRecord,
    TriageResult,
)

log = logging.getLogger("sieve.worker.scan")

REMOTE_DISABLED = "remote scanning disabled"


@dataclass
class _Loaded:
    tool_ids: dict[str, uuid.UUID]
    records: list[ToolRecord]
    findings_by_id: dict[str, list[ScannerFinding]]


async def _load(session: AsyncSession, scan_id: uuid.UUID) -> _Loaded:
    tools = list(
        (
            await session.execute(
                select(Tool).where(Tool.scan_id == scan_id).order_by(Tool.ordinal)
            )
        ).scalars()
    )
    ids = [t.id for t in tools]
    by_tool: dict[uuid.UUID, list[ScannerFinding]] = {}
    if ids:
        rows = (
            await session.execute(
                select(ScannerFindingRow).where(ScannerFindingRow.tool_id.in_(ids))
            )
        ).scalars()
        for row in rows:
            by_tool.setdefault(row.tool_id, []).append(
                ScannerFinding(
                    scanner=row.scanner,
                    scanner_version=row.scanner_ver,
                    rule_id=row.rule_id,
                    severity=row.severity,
                    message=row.message,
                    raw=row.raw,
                )
            )

    tool_ids: dict[str, uuid.UUID] = {}
    records: list[ToolRecord] = []
    findings_by_id: dict[str, list[ScannerFinding]] = {}
    for tool in tools:
        record_id = f"{tool.server_name or 'unknown'}/{tool.name}"
        tool_ids[record_id] = tool.id
        records.append(
            ToolRecord(
                id=record_id,
                server_name=tool.server_name or "unknown",
                tool=ToolDefinition(
                    name=tool.name,
                    title=tool.title,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                    outputSchema=tool.output_schema,
                    annotations=tool.annotations,
                ),
                sibling_tool_names=list(tool.siblings),
            )
        )
        if tool.id in by_tool:
            findings_by_id[record_id] = by_tool[tool.id]
    return _Loaded(tool_ids=tool_ids, records=records, findings_by_id=findings_by_id)


async def _reset(session: AsyncSession, scan_id: uuid.UUID) -> None:
    """A retried job re-triages from scratch; stale verdicts and events would double up."""
    tool_ids = select(Tool.id).where(Tool.scan_id == scan_id)
    await session.execute(delete(VerdictRow).where(VerdictRow.tool_id.in_(tool_ids)))
    await session.execute(delete(ScanEvent).where(ScanEvent.scan_id == scan_id))
    await session.commit()


async def _event(
    session: AsyncSession, scan_id: uuid.UUID, type_: str, payload: dict[str, object]
) -> None:
    session.add(ScanEvent(scan_id=scan_id, type=type_, payload=payload))
    await session.commit()


def _deps(budget: Callable[[], bool] | None = None) -> PipelineDeps:
    judge = get_judge() if settings.judge_enabled else None
    deps = PipelineDeps(
        classifier=get_classifier(), judge=judge, judge_enabled=settings.judge_enabled
    )
    if budget is not None:
        deps.budget = budget
    return deps


async def run(session: AsyncSession, job: Job) -> None:
    raw_id = job.payload.get("scan_id")
    if not isinstance(raw_id, str):
        raise ValueError("scan job payload must carry a string scan_id")
    scan_id = uuid.UUID(raw_id)
    scan = await session.get(Scan, scan_id)
    if scan is None:  # purged between enqueue and claim; nothing to do
        log.info("scan.missing", extra={"scan_id": raw_id})
        return

    await _reset(session, scan_id)
    scan.status = "running"
    scan.error = None
    await session.commit()
    await _event(session, scan_id, "scan.started", {"scan_id": raw_id})

    try:
        if scan.source_type == "remote_url":
            await _load_remote(session, scan)
        loaded = await _load(session, scan_id)
        results = await _triage(session, scan_id, loaded)
        await _finish(session, scan, results)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        scan.status = "failed"
        scan.error = detail
        await session.commit()
        await _event(session, scan_id, "scan.failed", {"scan_id": raw_id, "error": detail})
        log.warning("scan.failed", extra={"scan_id": raw_id, "error": type(exc).__name__})
        raise


async def _load_remote(session: AsyncSession, scan: Scan) -> None:
    """Enumerate an untrusted server -- only in the sandbox container, only when enabled (SC-1)."""
    if not settings.allow_remote_mcp:
        raise RuntimeError(REMOTE_DISABLED)
    from sieve.worker.remote import fetch_tools

    url = scan.source_ref or ""
    tools = await fetch_tools(url, timeout_s=settings.remote_timeout_seconds)
    if len(tools) > settings.max_tools_per_scan:
        raise ValueError(f"remote server advertised more than {settings.max_tools_per_scan} tools")

    server = urlsplit(url).hostname or "remote"
    names = [t.name for t in tools]
    for ordinal, tool in enumerate(tools):
        # Redact before insert, exactly as `DbStore.create_scan` does for a posted body (SC-5).
        clean, _kinds = redact_tool(tool)
        session.add(
            Tool(
                id=uuid.uuid4(),
                scan_id=scan.id,
                server_name=server,
                name=clean.name,
                title=clean.title,
                description=clean.description,
                input_schema=clean.inputSchema,
                output_schema=clean.outputSchema,
                annotations=clean.annotations,
                siblings=[n for n in names if n != clean.name],
                ordinal=ordinal,
            )
        )
    scan.tool_count = len(tools)
    await session.commit()


async def _triage(session: AsyncSession, scan_id: uuid.UUID, loaded: _Loaded) -> list[TriageResult]:
    loop = asyncio.get_running_loop()

    async def persist(record_id: str, result: TriageResult) -> None:
        tool_id = loaded.tool_ids[record_id]
        row = verdict_row(tool_id, result)
        session.add(row)
        if result.stage == "judge":
            # One transaction for the verdict and the money it cost (PRD §13.2), so a crash
            # can neither bank a free judge call nor charge for a verdict that was lost.
            await spend.charge(session)
        await session.commit()
        record = next(r for r in loaded.records if r.id == record_id)
        finding = Finding(
            id=str(row.id),
            scan_id=str(scan_id),
            tool=record.tool,
            server_name=record.server_name,
            sibling_tool_names=record.sibling_tool_names,
            result=result,
        )
        await _event(session, scan_id, "tool.verdict", json.loads(finding.model_dump_json()))

    def on_result(record_id: str, result: TriageResult) -> None:
        asyncio.run_coroutine_threadsafe(persist(record_id, result), loop).result()

    budget_ok = await spend.allowed(session)
    return await asyncio.to_thread(
        triage_many,
        loaded.records,
        loaded.findings_by_id,
        _deps(lambda: budget_ok),
        on_result=on_result,
        judge_budget=settings.judge_max_calls_per_scan,
    )


async def _finish(session: AsyncSession, scan: Scan, results: list[TriageResult]) -> None:
    scan.status = "complete"
    scan.confirmed = sum(1 for r in results if r.verdict == "confirmed")
    scan.needs_review = sum(1 for r in results if r.verdict == "needs_review")
    scan.dismissed = sum(1 for r in results if r.verdict == "dismissed")
    scan.judge_calls = sum(1 for r in results if r.stage == "judge")
    scan.judge_cost_usd = Decimal(str(spend.ESTIMATED_JUDGE_CALL_USD)) * scan.judge_calls
    scan.model_version = results[0].model_version if results else get_classifier().model_version
    scan.judge_model = next((r.judge_model for r in results if r.judge_model), None)
    await session.commit()
    await _event(
        session,
        scan.id,
        "scan.completed",
        {
            "scan_id": str(scan.id),
            "tool_count": scan.tool_count,
            "confirmed": scan.confirmed,
            "needs_review": scan.needs_review,
            "dismissed": scan.dismissed,
        },
    )
    log.info(
        "scan.completed",
        extra={"scan_id": str(scan.id), "tools": scan.tool_count, "confirmed": scan.confirmed},
    )
