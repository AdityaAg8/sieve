"""The `scan` job: load a scan's tools, triage them, stream verdicts, update the counters.

`triage_many` runs in a worker thread because a real DeBERTa forward pass is CPU-bound and
would otherwise stall the event loop; `on_result` hands each verdict back to the loop, so a
row and an SSE event land per tool rather than all 27 at the end.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, insert, select
from sqlalchemy import inspect as sa_inspect
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

#: Task 11.2: a 500-tool scan was issuing 500 round trips for a table that only needs 20. This
#: is the one authorised knob; the PRD does not pin a number.
VERDICT_BATCH_SIZE = 25

#: `verdicts.created_at` is server-side only (`func.now()`); `verdict_row()` never sets it, so
#: a bulk Core INSERT must leave it out too, or it would insert a literal NULL instead of
#: letting the default fire.
_VERDICT_INSERT_COLUMNS = [c.key for c in sa_inspect(VerdictRow).columns if c.key != "created_at"]


def _verdict_values(row: VerdictRow) -> dict[str, Any]:
    """`row`'s column values as a plain dict, for `insert(VerdictRow)` executed many-at-once."""
    return {key: getattr(row, key) for key in _VERDICT_INSERT_COLUMNS}


@dataclass
class _VerdictBatch:
    """Buffers verdict rows so `INSERT INTO verdicts` happens once per `VERDICT_BATCH_SIZE`
    rows instead of once per tool.

    `scan_events` is untouched by this: one row (and one `pg_notify`, PRD's `scan_events_notify`
    trigger) per verdict, added and committed only when its verdict row is -- flushing the two
    together is what keeps "never bank an uncharged verdict" true (tests/integration/
    test_judge_cost.py): a verdict's ledger charge already landed in this same open transaction
    (`spend.charge` does not commit) by the time it is buffered here, so a crash before the next
    flush loses the charge and the row together, and a commit lands both together.
    """

    session: AsyncSession
    scan_id: uuid.UUID
    batch_size: int = VERDICT_BATCH_SIZE
    _rows: list[VerdictRow] = field(default_factory=list)
    _payloads: list[dict[str, Any]] = field(default_factory=list)

    def add(self, row: VerdictRow, payload: dict[str, Any]) -> None:
        self._rows.append(row)
        self._payloads.append(payload)

    async def maybe_flush(self) -> None:
        if len(self._rows) >= self.batch_size:
            await self.flush()

    async def flush(self) -> None:
        if not self._rows:
            return
        rows, payloads = self._rows, self._payloads
        self._rows, self._payloads = [], []
        # `render_nulls=True`: without it, the ORM excludes NULL columns per row so each one's
        # server-side default still fires, but `reasoning`/`remediation`/`judge_model` are NULL
        # on every non-judge verdict -- a batch mixing judge and non-judge stages would
        # otherwise fragment back into one INSERT per row (SQLAlchemy 2.0.23+; verdicts never
        # rely on server defaults for these columns, so treating their NULLs as ordinary values
        # is exact, not an approximation).
        await self.session.execute(
            insert(VerdictRow).execution_options(render_nulls=True),
            [_verdict_values(r) for r in rows],
        )
        for payload in payloads:
            self.session.add(ScanEvent(scan_id=self.scan_id, type="tool.verdict", payload=payload))
        await self.session.commit()


@dataclass
class _Triaged:
    """The batch's verdicts and the money they cost, carried together to `_finish`."""

    results: list[TriageResult]
    judge_cost_usd: Decimal = Decimal(0)
    #: "usage" only when every judged decision was priced from the API's own token counts;
    #: one estimated call makes the scan's total an estimate. None when nothing was judged.
    cost_source: str | None = None


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
    # The judge holds the same ledger gate the pipeline does: the cap is process-wide, so it
    # has to hold for anything that reaches the judge, not only for the routed path.
    judge = get_judge(budget) if settings.judge_enabled else None
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
        triaged = await _triage(session, scan_id, loaded)
        await _finish(session, scan, triaged)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        # Whatever was half-written when this blew up is discarded first. A verdict row only
        # becomes durable when its `_VerdictBatch` flush commits, together with the charge that
        # paid for it (PRD §13.2) -- so the rollback either loses both or neither, never a
        # judged verdict with no ledger row beside it.
        await session.rollback()
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


async def _triage(session: AsyncSession, scan_id: uuid.UUID, loaded: _Loaded) -> _Triaged:
    loop = asyncio.get_running_loop()

    by_id = {r.id: r for r in loaded.records}
    deps = _deps(spend.budget_gate(session, loop))
    spent = Decimal(0)
    sources: set[str] = set()
    batch = _VerdictBatch(session, scan_id)

    async def persist(record_id: str, result: TriageResult) -> None:
        nonlocal spent
        tool_id = loaded.tool_ids[record_id]
        row = verdict_row(tool_id, result)
        if result.stage == "judge":
            # The judge answered this record a moment ago and the loop is sequential, so
            # `last_usage` is this call's usage -- what it actually cost, not the estimate.
            usd, source = spend.judge_call_cost(deps.judge)
            # Same (still open) transaction the verdict's batch will flush in (PRD §13.2), so a
            # crash before the next flush can neither bank a free judge call nor charge for a
            # verdict that was lost -- see `_VerdictBatch`.
            await spend.charge(session, usd)
            spent += Decimal(str(usd))
            sources.add(source)
        record = by_id[record_id]
        finding = Finding(
            id=str(row.id),
            scan_id=str(scan_id),
            tool=record.tool,
            server_name=record.server_name,
            sibling_tool_names=record.sibling_tool_names,
            result=result,
        )
        batch.add(row, finding.model_dump(mode="json"))
        await batch.maybe_flush()

    def on_result(record_id: str, result: TriageResult) -> None:
        asyncio.run_coroutine_threadsafe(persist(record_id, result), loop).result()

    results = await asyncio.to_thread(
        triage_many,
        loaded.records,
        loaded.findings_by_id,
        deps,
        on_result=on_result,
        judge_budget=settings.judge_max_calls_per_scan,
    )
    await batch.flush()
    return _Triaged(
        results=results,
        judge_cost_usd=spent,
        cost_source=("usage" if sources == {"usage"} else "estimate") if sources else None,
    )


async def _finish(session: AsyncSession, scan: Scan, triaged: _Triaged) -> None:
    results = triaged.results
    scan.status = "complete"
    scan.confirmed = sum(1 for r in results if r.verdict == "confirmed")
    scan.needs_review = sum(1 for r in results if r.verdict == "needs_review")
    scan.dismissed = sum(1 for r in results if r.verdict == "dismissed")
    scan.judge_calls = sum(1 for r in results if r.stage == "judge")
    # The sum of what each call was actually charged, so the scan and the ledger agree.
    scan.judge_cost_usd = triaged.judge_cost_usd
    scan.judge_cost_source = triaged.cost_source
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
