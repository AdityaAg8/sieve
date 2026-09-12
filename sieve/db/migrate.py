"""Programmatic `alembic upgrade head`, used by tests, the seed and `make up`."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from sieve.config import settings

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # `script_location` in the ini is repo-relative; make it absolute so the seed and the
    # worker can run from any working directory (the containers run from /app).
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "sieve" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or settings.database_url)
    return cfg


def upgrade_head(url: str | None = None) -> None:
    command.upgrade(alembic_config(url), "head")
