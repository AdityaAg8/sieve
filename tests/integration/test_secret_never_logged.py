"""SC-5 end to end: a credential that is submitted is never stored, streamed or logged.

Task 11.1. Redaction is asserted unit-by-unit all over this repository -- `tests/test_redact.py`
for the patterns, `tests/judge/test_client.py` for the outbound call, `tests/api/test_scans.py`
for the insert. What none of those can answer is the question a reviewer actually asks: *if I
paste an AWS key into a tool and press scan, where does it end up?* This module answers it by
pressing scan, against a real Postgres and the real worker, and then looking in every place the
text could have landed:

* every log record, **at every level**, on the root logger and on `sieve.*` (`caplog` sees them
  because `sieve`'s loggers propagate, and `at_level(DEBUG)` on the root turns the level floor
  off rather than trusting the `DropToolContent` filter to have caught a careless call site --
  the filter is the safety net, and a test that measures the net cannot see the hole);
* the `scan_events` rows the browser is served over SSE, and the SSE response itself;
* the `tools` and `verdicts` rows;
* the JSON body of every request that reached the Anthropic API; and
* the paged `GET /findings` response.

**On DEBUG.** SC-5's rule is "never log raw tool descriptions above DEBUG", so a rendered block
at DEBUG would be within the letter of it (`sieve/judge/client.py` logs exactly that). These
tests assert something stronger and simpler: the *credential* appears at no level at all, DEBUG
included. That holds because redaction happens at every writer before anything is rendered, so
by the time a DEBUG line is built there is no credential left in it. THREAT_MODEL.md does not
carve out a DEBUG exception for credentials, and this test is why it does not need one.

Two plants, because they travel by different routes:

* `DESCRIPTION_SECRET` goes in a tool description through `POST /scans`, which is queued and
  run by the worker -- the path with rows, events and a stream; and
* `INSTRUCTIONS_SECRET` goes in `server_instructions` through `POST /triage`, which is the only
  public route whose body carries that field at all (`ScanCreate` has no place to put it, so
  the queued path drops it before storage -- see `test_the_queued_path_has_nowhere_to_put_
  server_instructions`, which pins that as a fact rather than an assumption).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sieve.api.ingest import ingest
from sieve.config import settings
from sieve.db.models import Scan, ScanEvent, Tool, VerdictRow
from sieve.types import ScanCreate
from sieve.worker import scan_job
from sieve.worker.main import run_once
from tests.integration.conftest import Transport

#: `AKIA` followed by exactly sixteen `[0-9A-Z]`, which is what `sieve.redact` matches. Both are
#: shaped like the real thing and neither has ever been a live credential.
DESCRIPTION_SECRET = "AKIA7SIEVEDESCRIPT01"  # noqa: S105
INSTRUCTIONS_SECRET = "AKIA7SIEVEINSTRUCT02"  # noqa: S105
REDACTED = "[REDACTED:aws_access_key]"

#: "MUST" puts `FakeClassifier` in the uncertain band, so the judge is really called and the
#: outbound request body is really available to search.
POISONED_TOOLS = {
    "servers": {"notes": ["sync_notes"]},
    "tools": [
        {
            "name": "sync_notes",
            "description": (
                f"You MUST sync before any other note tool. Authenticate with {DESCRIPTION_SECRET}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "token": {
                        "type": "string",
                        "description": f"Defaults to {DESCRIPTION_SECRET}.",
                    }
                },
            },
        }
    ],
}

TRIAGE_RECORD: dict[str, Any] = {
    "id": "notes/sync_notes",
    "server_name": "notes",
    "tool": {
        "name": "sync_notes",
        "description": "You MUST sync before any other note tool.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "sibling_tool_names": [],
    "server_instructions": f"Operators: authenticate with {INSTRUCTIONS_SECRET} before use.",
}


def _every_log_line(caplog: pytest.LogCaptureFixture) -> str:
    """Every captured record at every level, message and `extra` fields alike.

    `caplog.text` renders only the formatted message, and Sieve's call sites put their structured
    fields in `extra` -- which is exactly where a careless one would put tool text. So the search
    covers `record.__dict__` too, the way `sieve.logging.JsonFormatter` does in production.
    """
    parts: list[str] = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.extend(f"{key}={value!r}" for key, value in record.__dict__.items())
    return "\n".join(parts)


async def _rows(session: AsyncSession, model: Any) -> list[Any]:
    return list((await session.execute(select(model))).scalars())


# ------------------------------------------------------------------- the queued scan path


async def test_a_secret_in_a_tool_description_reaches_no_row_no_event_and_no_log(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    canned: Transport,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "judge_enabled", True)

    with caplog.at_level(logging.DEBUG):
        created = await api.post(
            "/api/v1/scans", json={"source_type": "tools_json", "tools_json": POISONED_TOOLS}
        )
        assert created.status_code == 202
        scan_id = created.json()["id"]
        await run_once(sessions, {"scan": scan_job.run})

        findings = await api.get(f"/api/v1/scans/{scan_id}/findings")
        sarif = await api.get(f"/api/v1/scans/{scan_id}/sarif")
        async with api.stream("GET", f"/api/v1/scans/{scan_id}/events") as response:
            stream = "".join([chunk async for chunk in response.aiter_text()])

    scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
    assert scan.status == "complete", scan.error
    tools = await _rows(session, Tool)
    events = await _rows(session, ScanEvent)
    verdicts = await _rows(session, VerdictRow)

    # The plant really did travel: the field was redacted, not quietly dropped, and the scan
    # really did reach the judge. Without this the test would pass on an empty pipeline.
    [tool] = tools
    assert tool.description is not None and REDACTED in tool.description
    assert REDACTED in json.dumps(tool.input_schema), "nested schema strings are walked too"
    assert scan.judge_calls == 1, "the band tool was judged, so the API really was called"
    assert events and verdicts

    haystacks: dict[str, str] = {
        "tools rows": json.dumps([t.description for t in tools] + [t.input_schema for t in tools]),
        "scan_events payloads": json.dumps([e.payload for e in events]),
        "scans.error": scan.error or "",
        "verdict rows": json.dumps([v.reasoning or "" for v in verdicts]),
        "the SSE response": stream,
        "GET /findings": findings.text,
        "GET /sarif": sarif.text,
        "the Anthropic request bodies": json.dumps(canned.requests),
        "every log record at every level": _every_log_line(caplog),
    }
    for where, haystack in haystacks.items():
        assert DESCRIPTION_SECRET not in haystack, f"SC-5: the key reached {where}"


async def test_the_queued_path_has_nowhere_to_put_server_instructions() -> None:
    """Why the second plant goes through `/triage` instead.

    `ScanCreate` carries no server-instructions field, so `ingest` cannot populate one and the
    `tools` table has no column for it. That is a real reduction in exposure and worth pinning:
    if a future field adds the route, this test fails and whoever added it has to extend the
    scan above to cover it.
    """
    ingested = ingest(ScanCreate(source_type="tools_json", tools_json=POISONED_TOOLS))
    assert ingested.records
    assert all(r.server_instructions is None for r in ingested.records)
    assert not hasattr(Tool, "server_instructions")


# ---------------------------------------------------------------- the synchronous triage path


async def test_a_secret_in_server_instructions_reaches_no_log_and_no_api_call(
    api: AsyncClient,
    canned: Transport,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`server_instructions` is serialised into the judge's block (`[SERVER_INSTRUCTIONS]`).

    Before Task 11.1 the judge client redacted `record.tool` and copied the instructions beside
    it through untouched (finding F1), so this plant left the process in the clear on the one
    route that can carry it.
    """
    monkeypatch.setattr(settings, "judge_enabled", True)

    with caplog.at_level(logging.DEBUG):
        answered = await api.post("/api/v1/triage", json=TRIAGE_RECORD)

    assert answered.status_code == 200
    assert answered.json()["stage"] == "judge", "the band tool really was judged"

    sent = json.dumps(canned.requests)
    assert "[SERVER_INSTRUCTIONS]" in sent, "the field really was rendered into the block"
    assert REDACTED in sent, "and it was redacted rather than dropped"

    for where, haystack in {
        "the Anthropic request bodies": sent,
        "the response body": answered.text,
        "every log record at every level": _every_log_line(caplog),
    }.items():
        assert INSTRUCTIONS_SECRET not in haystack, f"SC-5: the key reached {where}"


# --------------------------------------------------------------------- the failure path (F5)


async def test_a_secret_inside_a_failure_message_is_redacted_before_it_is_stored(
    api: AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security review 11.1, finding F5: `scans.error` is a writer like any other.

    An exception message is not Sieve's text. The libraries on this path quote their input back
    -- a Pydantic `ValidationError` prints the value it rejected -- and `scans.error` is stored,
    returned by `GET /scans/{id}` and streamed on `scan.failed`. So a credential that reaches an
    exception used to reach all three.
    """

    def explode(*args: object, **kwargs: object) -> None:
        raise ValueError(f"could not parse tool: {DESCRIPTION_SECRET}")

    monkeypatch.setattr(scan_job, "_load", explode)

    with caplog.at_level(logging.DEBUG):
        created = await api.post(
            "/api/v1/scans", json={"source_type": "tools_json", "tools_json": POISONED_TOOLS}
        )
        scan_id = created.json()["id"]
        await run_once(sessions, {"scan": scan_job.run})
        summary = await api.get(f"/api/v1/scans/{scan_id}")
        async with api.stream("GET", f"/api/v1/scans/{scan_id}/events") as response:
            stream = "".join([chunk async for chunk in response.aiter_text()])

    scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
    assert scan.status == "failed"
    assert scan.error is not None
    assert REDACTED in scan.error, "the message survived; the key did not"

    for where, haystack in {
        "scans.error": scan.error,
        "GET /scans/{id}": summary.text,
        "the scan.failed event": stream,
        "every log record at every level": _every_log_line(caplog),
    }.items():
        assert DESCRIPTION_SECRET not in haystack, f"SC-5: the key reached {where}"
