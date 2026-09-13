"""SQLAlchemy 2 declarative models, column for column from PRD §18.

Nothing here invents a column. When §18 and this file disagree, §18 wins and the fix is a
migration plus an edit here in the same commit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    scanner_flagged: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    needs_review: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    dismissed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    judge_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    judge_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(8, 5), nullable=False, server_default=text("0")
    )
    #: "usage" (the API's own token counts) or "estimate" (PRD §12.4, a judge that reports
    #: none); NULL when the scan never called the judge.
    judge_cost_source: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    judge_model: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_scans_expires", "expires_at"),)


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    server_name: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)  # redacted before insert (SC-5)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    annotations: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    siblings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("idx_tools_scan", "scan_id", "ordinal"),)


class ScannerFindingRow(Base):
    __tablename__ = "scanner_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    scanner: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_ver: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class VerdictRow(Base):
    __tablename__ = "verdicts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    predicted_class: Mapped[str] = mapped_column(Text, nullable=False)
    class_probabilities: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    poisoned_probability: Mapped[float] = mapped_column(REAL, nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    sub_labels: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    owasp: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    evidence_spans: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    #: Quotes the judge gave that `sieve.judge.locate` could not find in the tool text.
    unlocated_quotes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reasoning: Mapped[str | None] = mapped_column(Text)
    remediation: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    judge_model: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_verdicts_tool", "tool_id"),)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    verdict_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("verdicts.id", ondelete="CASCADE"), nullable=False
    )
    agrees: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Partial: the reaper and `claim` only ever scan queued rows, and autogenerate would have
    # dropped the WHERE clause, so this index is hand-written in migration 0001 too.
    __table_args__ = (
        Index(
            "idx_jobs_ready",
            "status",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
    )


class ScanEvent(Base):
    __tablename__ = "scan_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_scan_events_scan", "scan_id", "id"),)


class RateLimit(Base):
    """Postgres-backed so limits survive a restart and a second process (PRD §13.2)."""

    __tablename__ = "rate_limits"

    ip_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (Index("idx_rate_limits_win", "window_start"),)


class SpendLedger(Base):
    """One row per UTC day; the hard daily judge cap (PRD §13.2)."""

    __tablename__ = "spend_ledger"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    judge_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    usd: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False, server_default=text("0"))
