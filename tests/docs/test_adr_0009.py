"""ADR-009 records every training-v1 deviation the plan lists (task I1)."""

from __future__ import annotations

from pathlib import Path

import pytest

ADR = Path("docs/adr/0009-training-v1-deviations.md")
PRD = Path("docs/superpowers/specs/2026-09-11-sieve-prd-v2.md")

#: Plan I1's list, one phrase per item, as the ADR must say it.
ITEMS = (
    "Ensemble labels instead of second-human κ (M8)",
    "intra-rater retest is dropped",
    "ensemble-vs-generator agreement",
    "6% hand-written share",
    "Two local generator families",
    "Leakage-gate history: 0.929",
    "Unit (c) is evaluation slicing",
    "Test-ID is real-only, and holdout-synthetic is the release gate",
    "M1 is reported for Stage 1",
    "M3 is",
    "M4 (judge cost) is n/a",
    "MCPTox and Docker are eval-only pointers",
    "bench/label/holdout_gold.py",
    "contracts:",
    "pyproject.toml",
    "`LABELLING_GUIDE.md` is drafted by Claude",
)


def test_every_plan_item_appears() -> None:
    text = ADR.read_text(encoding="utf-8")
    missing = [item for item in ITEMS if item not in text]
    assert not missing, missing


def test_every_contracts_commit_it_names_is_a_real_commit_subject() -> None:
    import re
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    text = ADR.read_text(encoding="utf-8")
    shas = set(re.findall(r"`([0-9a-f]{7})`", text))
    assert len(shas) >= 20
    for sha in sorted(shas):
        out = subprocess.run(  # noqa: S603 - fixed argv
            [git, "cat-file", "-t", sha], capture_output=True, text=True, check=False
        )
        assert out.stdout.strip() == "commit", sha


def test_the_adr_index_links_it() -> None:
    assert "(0009-training-v1-deviations.md)" in Path("docs/adr/README.md").read_text(
        encoding="utf-8"
    )


def test_the_prd_amendments_link_it() -> None:
    if not PRD.exists():
        pytest.skip("the PRD is a local, gitignored file")
    assert "adr/0009-training-v1-deviations.md" in PRD.read_text(encoding="utf-8")
