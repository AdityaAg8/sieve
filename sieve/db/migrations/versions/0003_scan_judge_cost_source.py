"""scans.judge_cost_source: whether judge_cost_usd was measured or estimated

A judge that reports `usage` is charged what it reported; one that reports none (the fake)
is charged PRD §12.4's estimate. The column says which, so a published cost figure is never
ambiguous. Nullable: a scan that never called the judge has no source, and every row that
existed before this migration was charged the estimate but is not rewritten here.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("judge_cost_source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scans", "judge_cost_source")
