"""The daily judge budget (PRD §13.2). One `spend_ledger` row per UTC day.

`charge` deliberately does NOT commit: PRD §13.2 requires it to run in the same transaction
as the verdict write, so a crash cannot bank a judge call without paying for it, or pay for
a call whose verdict was lost.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.config import settings
from sieve.protocols import Judge

# PRD §12.4's cost model (~1,800 cached + ~600 uncached input, ~250 output at Haiku 4.5
# pricing) and the committed sample scan, whose 2 judge calls cost US$0.0041, agree on this.
# It is the fallback for a judge that reports no usage at all -- `FakeJudge`, and the demo
# and every test that runs on it. A judge that reports usage is charged what it reported.
ESTIMATED_JUDGE_CALL_USD = 0.00205

#: Which of the two paid for a figure. Recorded on the scan so a published cost is never
#: ambiguous about whether it was measured or modelled.
CostSource = Literal["usage", "estimate"]

_CHARGE = """
INSERT INTO spend_ledger (day, judge_calls, usd)
VALUES (current_date, 1, :usd)
ON CONFLICT (day) DO UPDATE SET judge_calls = spend_ledger.judge_calls + 1,
                                usd = spend_ledger.usd + :usd
RETURNING usd
"""


async def spent_today(session: AsyncSession) -> Decimal:
    value = (
        await session.execute(text("SELECT usd FROM spend_ledger WHERE day = current_date"))
    ).scalar_one_or_none()
    return Decimal(0) if value is None else Decimal(value)


async def allowed(session: AsyncSession) -> bool:
    """True while today's spend is under the cap. This is `PipelineDeps.budget`."""
    return await spent_today(session) < Decimal(str(settings.daily_judge_budget_usd))


async def charge(session: AsyncSession, usd: float = ESTIMATED_JUDGE_CALL_USD) -> Decimal:
    """Record one judge call. The caller commits, together with the verdict it paid for."""
    total = (await session.execute(text(_CHARGE), {"usd": Decimal(str(usd))})).scalar_one()
    return Decimal(total)


def judge_call_cost(judge: Judge | None) -> tuple[float, CostSource]:
    """What the call this judge just made cost, and where the figure came from.

    `AnthropicJudge` records the API's own `usage` on every answered call, so the ledger
    accumulates measured spend rather than PRD §12.4's planning number -- a cap enforced on
    an estimate is a cap that does not hold. The estimate survives exactly one case: a judge
    with no usage to report. An unpriced model raises out of `sieve.judge.cost`; billing an
    unknown model as ~$0.002 would be a silent licence to spend.
    """
    usage = getattr(judge, "last_usage", None)
    model = getattr(judge, "model", None)
    if usage is None or not model:
        return ESTIMATED_JUDGE_CALL_USD, "estimate"
    # Imported here: `sieve.judge.cost` needs `anthropic`, the optional `judge` extra, and a
    # deployment running on the fake must not have to install it.
    from sieve.judge.cost import estimate

    return estimate(usage, model), "usage"


def budget_gate(session: AsyncSession, loop: asyncio.AbstractEventLoop) -> Callable[[], bool]:
    """A `PipelineDeps.budget` callback that asks the ledger at **every** decision.

    The pipeline is synchronous and runs in a worker thread, so the question has to be
    marshalled back to the event loop the same way `on_result` is. Reading the ledger once
    per scan would be worse than useless: a scan that crosses the cap on its third tool would
    keep spending for the rest of the batch, and concurrent scans would each freeze their own
    stale answer (PRD §13.2 makes the cap process-wide, not scan-wide).
    """

    def gate() -> bool:
        return asyncio.run_coroutine_threadsafe(allowed(session), loop).result()

    return gate
