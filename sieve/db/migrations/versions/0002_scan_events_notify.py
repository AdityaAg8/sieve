"""scan_events NOTIFY trigger so the SSE stream can LISTEN instead of polling

The payload is `<scan_id>:<event id>` -- an identifier pair, never event content, because
NOTIFY payloads are visible to anything connected to the database and a `tool.verdict` payload
carries tool text (SC-5). The stream reads the row it names.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL = "scan_events"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION scan_events_notify() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify('scan_events', NEW.scan_id::text || ':' || NEW.id::text);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER scan_events_notify
        AFTER INSERT ON scan_events
        FOR EACH ROW EXECUTE FUNCTION scan_events_notify();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS scan_events_notify ON scan_events;")
    op.execute("DROP FUNCTION IF EXISTS scan_events_notify();")
