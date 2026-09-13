"""The store boundary: one async protocol, a fixture-backed stub and the Postgres store.

Nothing above this boundary knows which one it is talking to, which is why the Phase 0
contract tests still run against `create_app(stub=True)` unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import Depends, Request
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.api.deps import FixtureStore, fixture_store
from sieve.api.ingest import Ingested
from sieve.config import settings
from sieve.db.models import Scan, ScanEvent, ScannerFindingRow, Tool, VerdictRow
from sieve.db.session import get_session
from sieve.redact import redact_tool
from sieve.types import (
    Finding,
    FindingPage,
    Label,
    ScanCreate,
    ScannerFinding,
    ScanSummary,
    Stage,
    SubLabel,
    ToolDefinition,
    TriageResult,
    Verdict,
)
from sieve.worker.queue import enqueue


class BadCursorError(ValueError):
    """A `cursor` query parameter that is not an offset. Surfaced as 400, never a 500."""


class Store(Protocol):
    async def create_scan(
        self, req: ScanCreate, data: Ingested, *, idempotency_key: str | None = None
    ) -> ScanSummary: ...

    async def get_scan(self, scan_id: str) -> ScanSummary | None: ...

    async def get_by_idempotency_key(self, key: str) -> ScanSummary | None: ...

    async def list_findings(
        self,
        scan_id: str,
        verdict: Verdict | None = None,
        predicted_class: Label | None = None,
        sub_label: SubLabel | None = None,
        stage: Stage | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FindingPage | None: ...

    async def all_findings(self, scan_id: str) -> list[Finding] | None: ...


def parse_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise BadCursorError(f"cursor must be an integer offset, got {cursor!r}") from exc
    if offset < 0:
        raise BadCursorError("cursor must not be negative")
    return offset


class StubStore:
    """Async façade over the Phase 0 `FixtureStore`, so `stub=True` keeps working verbatim."""

    def __init__(self, inner: FixtureStore) -> None:
        self._inner = inner
        self._by_key: dict[str, ScanSummary] = {}

    async def create_scan(
        self, req: ScanCreate, data: Ingested, *, idempotency_key: str | None = None
    ) -> ScanSummary:
        summary = self._inner.create_scan(req)
        if idempotency_key:
            self._by_key[idempotency_key] = summary
        return summary

    async def get_scan(self, scan_id: str) -> ScanSummary | None:
        return self._inner.get_scan(scan_id)

    async def get_by_idempotency_key(self, key: str) -> ScanSummary | None:
        return self._by_key.get(key)

    async def list_findings(
        self,
        scan_id: str,
        verdict: Verdict | None = None,
        predicted_class: Label | None = None,
        sub_label: SubLabel | None = None,
        stage: Stage | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FindingPage | None:
        parse_cursor(cursor)
        return self._inner.list_findings(
            scan_id, verdict, predicted_class, sub_label, stage, cursor, limit
        )

    async def all_findings(self, scan_id: str) -> list[Finding] | None:
        return self._inner.all_findings(scan_id)


def _summary(scan: Scan) -> ScanSummary:
    return ScanSummary(
        id=str(scan.id),
        status=scan.status,
        source_type=scan.source_type,
        tool_count=scan.tool_count,
        scanner_flagged=scan.scanner_flagged,
        confirmed=scan.confirmed,
        needs_review=scan.needs_review,
        dismissed=scan.dismissed,
        judge_calls=scan.judge_calls,
        judge_cost_usd=float(scan.judge_cost_usd),
        model_version=scan.model_version,
        judge_model=scan.judge_model,
        created_at=scan.created_at,
        expires_at=scan.expires_at,
        error=scan.error,
    )


def _tool_definition(tool: Tool) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        inputSchema=tool.input_schema,
        outputSchema=tool.output_schema,
        annotations=tool.annotations,
    )


def _scanner_finding(row: ScannerFindingRow) -> ScannerFinding:
    return ScannerFinding(
        scanner=row.scanner,
        scanner_version=row.scanner_ver,
        rule_id=row.rule_id,
        severity=row.severity,
        message=row.message,
        raw=row.raw,
    )


def _triage_result(row: VerdictRow, findings: list[ScannerFinding]) -> TriageResult:
    return TriageResult(
        verdict=row.verdict,
        stage=row.stage,
        predicted_class=row.predicted_class,
        class_probabilities={k: float(v) for k, v in row.class_probabilities.items()},
        poisoned_probability=row.poisoned_probability,
        confidence=row.confidence,
        sub_labels=list(row.sub_labels),
        owasp=list(row.owasp),
        evidence_spans=row.evidence_spans,
        reasoning=row.reasoning,
        remediation=row.remediation,
        scanner_findings=findings,
        model_version=row.model_version,
        judge_model=row.judge_model,
        latency_ms=row.latency_ms or 0,
    )


def verdict_row(tool_id: uuid.UUID, result: TriageResult) -> VerdictRow:
    """The one place a `TriageResult` becomes a row; the worker and the seed both use it."""
    return VerdictRow(
        id=uuid.uuid4(),
        tool_id=tool_id,
        verdict=result.verdict,
        stage=result.stage,
        predicted_class=result.predicted_class,
        class_probabilities=dict(result.class_probabilities),
        poisoned_probability=result.poisoned_probability,
        confidence=result.confidence,
        sub_labels=list(result.sub_labels),
        owasp=list(result.owasp),
        evidence_spans=[s.model_dump() for s in result.evidence_spans],
        reasoning=result.reasoning,
        remediation=result.remediation,
        model_version=result.model_version,
        judge_model=result.judge_model,
        latency_ms=result.latency_ms,
    )


class DbStore:
    """Postgres-backed store. One instance per request, holding that request's session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- writes ---------------------------------------------------------------------

    async def create_scan(
        self, req: ScanCreate, data: Ingested, *, idempotency_key: str | None = None
    ) -> ScanSummary:
        now = datetime.now(UTC)
        scan = Scan(
            id=uuid.uuid4(),
            source_type=req.source_type,
            source_ref=data.source_ref,
            status="queued",
            tool_count=len(data.records),
            scanner_flagged=sum(1 for v in data.findings_by_id.values() if v),
            idempotency_key=idempotency_key,
            expires_at=now + timedelta(days=settings.scan_ttl_days),
        )
        self.session.add(scan)
        tool_ids: list[tuple[str, uuid.UUID]] = []
        for ordinal, record in enumerate(data.records):
            # §18: `description` is redacted *before* insert, so a credential pasted into a
            # tool description never reaches the database at all (SC-5).
            clean, _kinds = redact_tool(record.tool)
            tool = Tool(
                id=uuid.uuid4(),
                scan_id=scan.id,
                server_name=record.server_name,
                name=clean.name,
                title=clean.title,
                description=clean.description,
                input_schema=clean.inputSchema,
                output_schema=clean.outputSchema,
                annotations=clean.annotations,
                siblings=list(record.sibling_tool_names),
                ordinal=ordinal,
            )
            self.session.add(tool)
            tool_ids.append((record.id, tool.id))

        # Flush the parents before their children: these models carry foreign keys but no
        # `relationship()`, so nothing tells the unit of work which insert has to go first.
        await self.session.flush()
        for record_id, tool_id in tool_ids:
            for finding in data.findings_by_id.get(record_id, []):
                self.session.add(
                    ScannerFindingRow(
                        id=uuid.uuid4(),
                        tool_id=tool_id,
                        scanner=finding.scanner,
                        scanner_ver=finding.scanner_version,
                        rule_id=finding.rule_id,
                        severity=finding.severity,
                        message=finding.message,
                        raw=finding.raw,
                    )
                )
        try:
            await self.session.commit()
        except IntegrityError:
            # Concurrent request with the same Idempotency-Key won the insert.
            await self.session.rollback()
            if idempotency_key:
                existing = await self.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise
        # The row is durable before the job exists, so a crash between the two leaves a
        # `queued` scan a human can re-enqueue -- never a job pointing at nothing.
        await enqueue(self.session, "scan", {"scan_id": str(scan.id)})
        return _summary(scan)

    async def record_event(self, scan_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> None:
        self.session.add(ScanEvent(scan_id=scan_id, type=type_, payload=payload))
        await self.session.commit()

    # -- reads ----------------------------------------------------------------------

    async def get_scan(self, scan_id: str) -> ScanSummary | None:
        try:
            key = uuid.UUID(scan_id)
        except ValueError:
            return None
        scan = await self.session.get(Scan, key)
        return None if scan is None else _summary(scan)

    async def get_by_idempotency_key(self, key: str) -> ScanSummary | None:
        scan = (
            await self.session.execute(select(Scan).where(Scan.idempotency_key == key))
        ).scalar_one_or_none()
        return None if scan is None else _summary(scan)

    async def _scan_exists(self, scan_id: uuid.UUID) -> bool:
        return (
            await self.session.execute(select(Scan.id).where(Scan.id == scan_id))
        ).scalar_one_or_none() is not None

    async def _findings_for(
        self, rows: Sequence[tuple[Tool, VerdictRow]], scan_id: uuid.UUID
    ) -> list[Finding]:
        tool_ids = [tool.id for tool, _ in rows]
        by_tool: dict[uuid.UUID, list[ScannerFinding]] = {}
        if tool_ids:
            scanner_rows = (
                await self.session.execute(
                    select(ScannerFindingRow).where(ScannerFindingRow.tool_id.in_(tool_ids))
                )
            ).scalars()
            for row in scanner_rows:
                by_tool.setdefault(row.tool_id, []).append(_scanner_finding(row))
        return [
            Finding(
                id=str(verdict.id),
                scan_id=str(scan_id),
                tool=_tool_definition(tool),
                server_name=tool.server_name,
                sibling_tool_names=list(tool.siblings),
                result=_triage_result(verdict, by_tool.get(tool.id, [])),
            )
            for tool, verdict in rows
        ]

    def _filtered(
        self,
        stmt: Select[Any],
        verdict: Verdict | None,
        predicted_class: Label | None,
        sub_label: SubLabel | None,
        stage: Stage | None,
    ) -> Select[Any]:
        if verdict is not None:
            stmt = stmt.where(VerdictRow.verdict == verdict)
        if predicted_class is not None:
            stmt = stmt.where(VerdictRow.predicted_class == predicted_class)
        if sub_label is not None:
            stmt = stmt.where(VerdictRow.sub_labels.contains([sub_label]))
        if stage is not None:
            stmt = stmt.where(VerdictRow.stage == stage)
        return stmt

    async def list_findings(
        self,
        scan_id: str,
        verdict: Verdict | None = None,
        predicted_class: Label | None = None,
        sub_label: SubLabel | None = None,
        stage: Stage | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> FindingPage | None:
        offset = parse_cursor(cursor)
        try:
            key = uuid.UUID(scan_id)
        except ValueError:
            return None
        if not await self._scan_exists(key):
            return None

        base = (
            select(Tool, VerdictRow)
            .join(VerdictRow, VerdictRow.tool_id == Tool.id)
            .where(Tool.scan_id == key)
        )
        base = self._filtered(base, verdict, predicted_class, sub_label, stage)
        total = (
            await self.session.execute(
                self._filtered(
                    select(func.count())
                    .select_from(Tool)
                    .join(VerdictRow, VerdictRow.tool_id == Tool.id)
                    .where(Tool.scan_id == key),
                    verdict,
                    predicted_class,
                    sub_label,
                    stage,
                )
            )
        ).scalar_one()

        rows = list(
            (
                await self.session.execute(base.order_by(Tool.ordinal).offset(offset).limit(limit))
            ).all()
        )
        items = await self._findings_for([(r[0], r[1]) for r in rows], key)
        next_cursor = str(offset + limit) if offset + limit < total else None
        return FindingPage(items=items, total=total, next_cursor=next_cursor)

    async def all_findings(self, scan_id: str) -> list[Finding] | None:
        page = await self.list_findings(scan_id, limit=settings.max_tools_per_scan)
        return None if page is None else page.items


async def get_store(request: Request, session: AsyncSession = Depends(get_session)) -> Store:
    """`create_app(stub=True)` keeps the Phase 0 fixture store; everything else hits Postgres."""
    if getattr(request.app.state, "stub", False):
        return StubStore(fixture_store())
    return DbStore(session)
