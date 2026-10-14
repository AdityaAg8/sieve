"""Every `make` command CONTRIBUTING.md names exists in the Makefile."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DOC = Path("CONTRIBUTING.md")


@pytest.fixture(scope="module")
def targets() -> set[str]:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    out = subprocess.run(  # noqa: S603 - fixed argv
        [make, "-s", "--no-print-directory", "help"], capture_output=True, text=True, check=True
    )
    return {line.split()[0] for line in out.stdout.splitlines() if line.startswith("  ")}


def test_every_make_command_exists(targets: set[str]) -> None:
    named = set(re.findall(r"make ([a-z][a-z0-9_-]*)", DOC.read_text(encoding="utf-8")))
    assert named, DOC
    assert named <= targets, sorted(named - targets)
