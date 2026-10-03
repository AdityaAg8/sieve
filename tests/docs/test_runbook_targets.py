"""Every `make` command the runbook and the training guide name exists (training plan I4)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DOCS = (Path("docs/RUNBOOK.md"), Path("docs/TRAINING_GUIDE.md"))
#: Plan I4: the new targets are documented, not just defined.
NEW = (
    "corpus", "gate", "dedup-cross", "baseline", "pairs", "tfidf-ood", "review-queue",
    "label-dev-audit", "label-test-id", "probes", "gpu-bench", "sweep", "select", "export",
    "eval-final", "bench", "reproduce-check", "readme", "push", "latency-http", "plan-evidence",
)  # fmt: skip


@pytest.fixture(scope="module")
def targets() -> set[str]:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    out = subprocess.run(  # noqa: S603 - fixed argv
        [make, "-s", "--no-print-directory", "help"], capture_output=True, text=True, check=True
    )
    return {line.split()[0] for line in out.stdout.splitlines() if line.startswith("  ")}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_make_command_exists(doc: Path, targets: set[str]) -> None:
    if not doc.exists():
        pytest.skip(f"{doc} is a local, gitignored file")
    named = set(re.findall(r"`make ([a-z][a-z0-9_-]*)", doc.read_text(encoding="utf-8")))
    assert named, doc
    assert named <= targets, sorted(named - targets)


def test_the_runbook_documents_every_new_target() -> None:
    named = set(re.findall(r"make ([a-z][a-z0-9_-]*)", Path(DOCS[0]).read_text(encoding="utf-8")))
    assert set(NEW) <= named, sorted(set(NEW) - named)
