"""verdicts.unlocated_quotes: how many judge quotes could not be found in the tool text

`sieve.judge.locate` keeps a quote it cannot locate, with null offsets, and counts it. The
count is a quality signal about the judge, so it is stored beside the verdict rather than
recomputed from the spans -- the spans are what the judge said, this is what that cost.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "verdicts",
        sa.Column(
            "unlocated_quotes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("verdicts", "unlocated_quotes")
