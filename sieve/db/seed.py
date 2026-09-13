"""`python -m sieve.db.seed`: the pre-computed sample scan, so `make up` has content at once.

It is also how the judge story gets told on the public demo, where `SIEVE_JUDGE_ENABLED` is
false: the fixture's two judge-stage verdicts were recorded once and are replayed, never
re-called (PRD §13.4).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.api.store import verdict_row
from sieve.config import settings
from sieve.db.migrate import upgrade_head
from sieve.db.models import Scan, ScanEvent, ScannerFindingRow, Tool
from sieve.db.session import create_engine, use_selector_event_loop_on_windows
from sieve.redact import redact_tool
from sieve.types import Finding, TriageResult

FIXTURE = Path("fixtures/verdicts/sample-27.json")

# A fixed id so `make up` always links to the same scan and a re-seed replaces it in place.
SEED_SCAN_ID = uuid.UUID(int=0x5EED)


def _document() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


async def seed(session: AsyncSession) -> uuid.UUID:
    """Insert the sample scan, replacing any previous copy. Safe to run any number of times."""
    document = _document()
    summary = document["summary"]
    findings = [Finding.model_validate(f) for f in document["findings"]]

    # Delete first: `tools`, `verdicts` and `scan_events` all cascade from `scans` (§18), so
    # this is the whole idempotency story, and a re-seed after a fixture edit is honest.
    await session.execute(delete(Scan).where(Scan.id == SEED_SCAN_ID))
    await session.flush()

    now = datetime.now(UTC)
    session.add(
        Scan(
            id=SEED_SCAN_ID,
            source_type=summary["source_type"],
            source_ref="fixtures/verdicts/sample-27.json",
            status="complete",
            tool_count=summary["tool_count"],
            scanner_flagged=summary["scanner_flagged"],
            confirmed=summary["confirmed"],
            needs_review=summary["needs_review"],
            dismissed=summary["dismissed"],
            judge_calls=summary["judge_calls"],
            judge_cost_usd=summary["judge_cost_usd"],
            model_version=summary["model_version"],
            judge_model=summary["judge_model"],
            created_at=now,
            expires_at=now + timedelta(days=settings.scan_ttl_days),
        )
    )
    session.add(ScanEvent(scan_id=SEED_SCAN_ID, type="scan.started", payload={"seeded": True}))

    tools: list[tuple[Tool, Finding]] = []
    for ordinal, finding in enumerate(findings):
        clean, _kinds = redact_tool(finding.tool)
        tool = Tool(
            id=uuid.uuid4(),
            scan_id=SEED_SCAN_ID,
            server_name=finding.server_name,
            name=clean.name,
            title=clean.title,
            description=clean.description,
            input_schema=clean.inputSchema,
            output_schema=clean.outputSchema,
            annotations=clean.annotations,
            siblings=list(finding.sibling_tool_names),
            ordinal=ordinal,
        )
        session.add(tool)
        tools.append((tool, finding))
    await session.flush()  # parents before children: no relationships to order them for us

    for tool, finding in tools:
        result: TriageResult = finding.result
        for scanner_finding in result.scanner_findings:
            session.add(
                ScannerFindingRow(
                    id=uuid.uuid4(),
                    tool_id=tool.id,
                    scanner=scanner_finding.scanner,
                    scanner_ver=scanner_finding.scanner_version,
                    rule_id=scanner_finding.rule_id,
                    severity=scanner_finding.severity,
                    message=scanner_finding.message,
                    raw=scanner_finding.raw,
                )
            )
        row = verdict_row(tool.id, result)
        session.add(row)
        await session.flush()
        seeded = Finding(
            id=str(row.id),
            scan_id=str(SEED_SCAN_ID),
            tool=finding.tool,
            server_name=finding.server_name,
            sibling_tool_names=finding.sibling_tool_names,
            result=result,
        )
        session.add(
            ScanEvent(
                scan_id=SEED_SCAN_ID,
                type="tool.verdict",
                payload=seeded.model_dump(mode="json"),
            )
        )

    session.add(
        ScanEvent(
            scan_id=SEED_SCAN_ID,
            type="scan.completed",
            payload={
                "scan_id": str(SEED_SCAN_ID),
                "tool_count": summary["tool_count"],
                "confirmed": summary["confirmed"],
                "needs_review": summary["needs_review"],
                "dismissed": summary["dismissed"],
            },
        )
    )
    await session.commit()
    return SEED_SCAN_ID


async def count_seeded(session: AsyncSession) -> int:
    return len(
        list((await session.execute(select(Tool.id).where(Tool.scan_id == SEED_SCAN_ID))).scalars())
    )


async def _main() -> None:
    engine = create_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            scan_id = await seed(session)
    finally:
        await engine.dispose()
    print(f"seeded sample scan {scan_id}")  # noqa: T201 -- this is a CLI


def main() -> None:  # pragma: no cover -- process entry point
    use_selector_event_loop_on_windows()
    # Alembic's env.py opens its own loop, so migrate before we own one of our own.
    upgrade_head()
    asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover
    main()
