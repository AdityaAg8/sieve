"""initial schema: scans, tools, findings, verdicts, feedback, jobs, events, limits, ledger

Hand-checked against PRD §18 column for column. Autogenerate does not emit the partial
`idx_jobs_ready` index or the composite `rate_limits` primary key the way §18 writes them,
so this file is authored, not generated.

Revision ID: 0001
Revises:
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("tool_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("scanner_flagged", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("confirmed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("needs_review", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("dismissed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("judge_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("judge_cost_usd", sa.Numeric(8, 5), nullable=False, server_default=sa.text("0")),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True, unique=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_scans_expires", "scans", ["expires_at"])

    op.create_table(
        "tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("server_name", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), nullable=True),
        sa.Column("annotations", postgresql.JSONB(), nullable=True),
        sa.Column(
            "siblings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
    )
    op.create_index("idx_tools_scan", "tools", ["scan_id", "ordinal"])

    op.create_table(
        "scanner_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tool_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scanner", sa.Text(), nullable=False),
        sa.Column("scanner_ver", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "verdicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tool_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("predicted_class", sa.Text(), nullable=False),
        sa.Column("class_probabilities", postgresql.JSONB(), nullable=False),
        sa.Column("poisoned_probability", postgresql.REAL(), nullable=False),
        sa.Column("confidence", postgresql.REAL(), nullable=False),
        sa.Column(
            "sub_labels",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "owasp", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "evidence_spans", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_verdicts_tool", "verdicts", ["tool_id"])

    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "verdict_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("verdicts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agrees", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Partial by design: `claim` and `reap` never look at anything but queued rows.
    op.create_index(
        "idx_jobs_ready",
        "jobs",
        ["status", "run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )

    op.create_table(
        "scan_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_scan_events_scan", "scan_events", ["scan_id", "id"])

    op.create_table(
        "rate_limits",
        sa.Column("ip_hash", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("ip_hash", "bucket", "window_start"),
    )
    op.create_index("idx_rate_limits_win", "rate_limits", ["window_start"])

    op.create_table(
        "spend_ledger",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("judge_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("usd", sa.Numeric(10, 5), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_table("spend_ledger")
    op.drop_index("idx_rate_limits_win", table_name="rate_limits")
    op.drop_table("rate_limits")
    op.drop_index("idx_scan_events_scan", table_name="scan_events")
    op.drop_table("scan_events")
    op.drop_index("idx_jobs_ready", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("feedback")
    op.drop_index("idx_verdicts_tool", table_name="verdicts")
    op.drop_table("verdicts")
    op.drop_table("scanner_findings")
    op.drop_index("idx_tools_scan", table_name="tools")
    op.drop_table("tools")
    op.drop_index("idx_scans_expires", table_name="scans")
    op.drop_table("scans")
