"""`python -m sieve.api` actually serves, on this platform, against a real database.

On Windows this is the regression test for the uvicorn loop: uvicorn 0.36+ builds its own
event loop and ignores the policy `create_app` installs, so a plain server got a
`ProactorEventLoop` and psycopg-async refused it -- `/api/v1/readyz` reported the database
down while every in-process test passed.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import httpx
import pytest

from sieve.api.loop import LOOP_FACTORY_PATH, new_event_loop

STARTUP_TIMEOUT_S = 60.0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_the_factory_returns_a_loop_psycopg_can_use() -> None:
    loop = new_event_loop()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert "Proactor" not in type(loop).__name__
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_each_call_returns_a_distinct_loop() -> None:
    """uvicorn's contract for a custom `--loop` path: a factory, not a shared loop."""
    first, second = new_event_loop(), new_event_loop()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()


def test_uvicorn_resolves_the_configured_loop_path() -> None:
    """The `module:function` string is what `--loop` and `Config(loop=...)` both take.

    For a custom path uvicorn returns the imported object *as* the factory -- it does not
    call it with `use_subprocess=` the way it does its own built-ins -- so registering a
    `factory-returning-a-factory` here would hand uvicorn a class where it wants a loop.
    """
    from uvicorn.config import Config

    config = Config("sieve.api.main:app", loop=LOOP_FACTORY_PATH)
    make_loop = config.get_loop_factory()
    assert make_loop is not None
    loop = make_loop()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop), "uvicorn must get a loop, not a class"
        assert "Proactor" not in type(loop).__name__
    finally:
        loop.close()


def test_the_entry_point_passes_the_app_as_an_import_string() -> None:
    """An app object would break `--reload`, which is the whole point of keeping the string."""
    from sieve.api.__main__ import APP, parse_args

    assert APP == "sieve.api.main:app"
    assert parse_args(["--reload", "--port", "9001"]).reload is True
    assert parse_args([]).reload is False


@pytest.fixture
def served(migrated: str) -> Iterator[str]:
    """`python -m sieve.api` on a free port, against the live database.

    The child's output is **drained** on a reader thread, not merely piped. uvicorn logs an
    access line per request to a pipe nobody reads, and a pipe nobody reads has a bounded OS
    buffer (~64 KB): fill it and the server blocks inside `write()` for as long as this fixture
    lives, which presents as a server that answered and then stopped. The lines are kept so a
    failure can say what the server said instead of nothing at all.
    """
    import os

    port = free_port()
    env = {**os.environ, "DATABASE_URL": migrated, "PYTHONUTF8": "1"}
    process = subprocess.Popen(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-m", "sieve.api", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=env,
    )
    log: list[str] = []

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            log.append(line.rstrip("\n"))

    reader = threading.Thread(target=drain, name=f"sieve-api-{port}-stdout", daemon=True)
    reader.start()

    with process:  # closes the stdout pipe, so the fixture leaves no open handle behind
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
                process.wait(timeout=15)
            # The child is gone, so the pipe is at EOF and the reader returns. Joined rather
            # than abandoned: a daemon thread still holding the handle is the leak this
            # `with process:` exists to avoid.
            reader.join(timeout=15)


def wait_for(url: str, process_is_alive: object = None) -> httpx.Response:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:  # not listening yet
            last = exc
            time.sleep(0.25)
    raise AssertionError(f"server never answered {url}: {last}")


def test_the_served_api_reaches_postgres(served: str) -> None:
    response = wait_for(f"{served}/api/v1/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["db"] == "ok", f"the server could not reach Postgres: {body}"
    assert body["ok"] is True
    assert body["model"]


def test_the_served_api_answers_a_real_query(served: str) -> None:
    wait_for(f"{served}/api/v1/readyz")
    models = httpx.get(f"{served}/api/v1/models", timeout=10)
    assert models.status_code == 200
    assert models.json()["model_version"]
    # The security headers are middleware, so this also proves the real ASGI stack is wired.
    assert models.headers["X-Content-Type-Options"] == "nosniff"
