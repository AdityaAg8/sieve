"""The two Windows defects a skipped Postgres had been hiding.

Both are regressions waiting to happen: the Alembic one only bites when warnings are errors,
the psycopg one only bites on win32, and CI is Linux.
"""

from __future__ import annotations

import asyncio
import configparser
import sys
import warnings

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sieve.db.migrate import ALEMBIC_INI, alembic_config
from sieve.db.session import use_selector_event_loop_on_windows


def test_alembic_ini_sets_an_explicit_path_separator() -> None:
    """Without it Alembic >= 1.16 warns, and `filterwarnings = ["error"]` fails every DB test."""
    parser = configparser.ConfigParser()
    parser.read(ALEMBIC_INI, encoding="utf-8")
    assert parser.get("alembic", "path_separator") == "os"
    assert parser.get("alembic", "prepend_sys_path") == "."


def test_building_the_alembic_config_emits_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = alembic_config("postgresql+psycopg://sieve:sieve@127.0.0.1:5433/sieve")
    assert cfg.get_main_option("script_location", "").endswith("migrations")


@pytest.mark.skipif(sys.platform != "win32", reason="the Proactor loop only exists on Windows")
def test_selector_policy_is_installed_on_windows() -> None:
    use_selector_event_loop_on_windows()
    assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


def test_selector_policy_is_idempotent() -> None:
    before = asyncio.get_event_loop_policy()
    use_selector_event_loop_on_windows()
    use_selector_event_loop_on_windows()
    assert type(asyncio.get_event_loop_policy()) is type(before)


async def test_async_db_tests_do_not_run_on_a_proactor_loop(session: AsyncSession) -> None:
    """The symptom was "Psycopg cannot use the 'ProactorEventLoop' to run in async mode"."""
    loop = asyncio.get_running_loop()
    assert "Proactor" not in type(loop).__name__
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
