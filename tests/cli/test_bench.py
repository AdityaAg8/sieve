"""`sieve bench reproduce` (Task 7.2): a thin `subprocess` wrapper around `make bench`."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sieve.cli.app import EXIT_MODEL, EXIT_OK, app

runner = CliRunner()


def test_bench_reproduce_runs_make_bench(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(app, ["bench", "reproduce"])
    assert result.exit_code == EXIT_OK, result.output
    assert calls == [["make", "bench"]]


def test_bench_reproduce_propagates_a_failing_make(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, *, check: SimpleNamespace(returncode=2))
    result = runner.invoke(app, ["bench", "reproduce"])
    assert result.exit_code == EXIT_MODEL


def test_bench_reproduce_reports_missing_make(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], *, check: bool) -> SimpleNamespace:
        raise FileNotFoundError("make")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(app, ["bench", "reproduce"])
    assert result.exit_code == EXIT_MODEL
    assert "make" in result.output.lower()
