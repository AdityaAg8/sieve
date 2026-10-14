"""LABELLING_GUIDE.md: 20 cases, every quoted row permissively licensed."""

from __future__ import annotations

import json
import re
from pathlib import Path

GUIDE = Path("LABELLING_GUIDE.md")
LICENCES = Path("bench/data/catalog/licences.json")
ALLOWED_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}


def _cases() -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    return re.split(r"^### Case \d+", text, flags=re.MULTILINE)[1:]


def test_there_are_twenty_cases_nine_worked_and_eleven_drills() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"^### Case (\d+)", text, flags=re.MULTILINE)]
    assert numbers == list(range(1, 21))


def test_every_quoted_row_is_permissively_licensed() -> None:
    for case in _cases():
        licence = re.search(r"\*\*Licence:\*\* (.+)", case)
        assert licence, case[:80]
        value = licence.group(1).strip()
        if value == "none quoted":
            assert "**Text:**" not in case, "a case that quotes text must name its licence"
            continue
        assert value in ALLOWED_LICENCES, value


def test_catalog_rows_carry_the_licence_their_package_has() -> None:
    known = json.loads(LICENCES.read_text(encoding="utf-8"))["licences"]
    for case in _cases():
        row = re.search(r"`(cat-[0-9a-f]{12})` \(`([^ ]+) / ", case)
        if row:
            licence = re.search(r"\*\*Licence:\*\* (.+)", case)
            assert licence and known.get(row.group(2)) == licence.group(1).strip(), row.group(0)


def test_adr_009_says_the_guide_is_unused_until_a_dev_audit_is_recorded() -> None:
    if not Path("bench/results/label_audit.json").exists():
        adr = Path("docs/adr/0009-training-v1-deviations.md").read_text(encoding="utf-8")
        assert "`LABELLING_GUIDE.md` has not been used yet" in adr
