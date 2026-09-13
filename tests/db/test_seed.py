"""`make up` content: the pre-computed sample scan, seeded idempotently."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.db.models import Scan, ScanEvent, Tool, VerdictRow
from sieve.db.seed import SEED_SCAN_ID, count_seeded, seed


async def test_seeding_inserts_the_sample_scan(session: AsyncSession) -> None:
    scan_id = await seed(session)
    assert scan_id == SEED_SCAN_ID

    scan = await session.get(Scan, SEED_SCAN_ID)
    assert scan is not None
    assert scan.status == "complete"
    assert scan.tool_count == 27
    assert scan.confirmed + scan.needs_review + scan.dismissed == 27
    assert scan.judge_model == "claude-haiku-4-5"
    assert await count_seeded(session) == 27


async def test_seeding_twice_leaves_exactly_one_sample_scan(session: AsyncSession) -> None:
    await seed(session)
    await seed(session)

    scans = (await session.execute(select(func.count()).select_from(Scan))).scalar_one()
    tools = (await session.execute(select(func.count()).select_from(Tool))).scalar_one()
    verdicts = (await session.execute(select(func.count()).select_from(VerdictRow))).scalar_one()
    assert (scans, tools, verdicts) == (1, 27, 27)


async def test_the_seed_writes_a_replayable_event_trail(session: AsyncSession) -> None:
    await seed(session)
    kinds = list(
        (
            await session.execute(
                select(ScanEvent.type)
                .where(ScanEvent.scan_id == SEED_SCAN_ID)
                .order_by(ScanEvent.id)
            )
        ).scalars()
    )
    assert kinds == ["scan.started", *["tool.verdict"] * 27, "scan.completed"]


async def test_the_seeded_judge_verdicts_are_replayed_not_recalled(session: AsyncSession) -> None:
    """The judge story with the judge off (PRD §13.4): the fixture's stages survive."""
    await seed(session)
    stages = list(
        (
            await session.execute(
                select(VerdictRow.stage)
                .join(Tool, Tool.id == VerdictRow.tool_id)
                .where(Tool.scan_id == SEED_SCAN_ID)
            )
        ).scalars()
    )
    assert stages.count("judge") == 2
    assert set(stages) <= {"classifier", "judge", "judge_failed", "judge_budget_exhausted"}


async def test_the_seeded_tools_are_redacted(session: AsyncSession) -> None:
    await seed(session)
    descriptions = list(
        (
            await session.execute(select(Tool.description).where(Tool.scan_id == SEED_SCAN_ID))
        ).scalars()
    )
    assert not any(d and "AKIA" in d for d in descriptions)


async def test_the_seed_runs_as_a_script_twice(migrated: str, session: AsyncSession) -> None:
    """`make up` runs `python -m sieve.db.seed`; it must migrate and seed in one process."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "DATABASE_URL": migrated, "PYTHONUTF8": "1"}
    for _ in range(2):
        done = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [sys.executable, "-m", "sieve.db.seed"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert str(SEED_SCAN_ID) in done.stdout

    scans = (await session.execute(select(func.count()).select_from(Scan))).scalar_one()
    assert scans == 1
