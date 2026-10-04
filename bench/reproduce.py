"""Compare regenerated ablation rows against the committed ones, byte for byte.

The second half of the clean-clone reproduction gate
(`tests/integration/test_make_bench_clean_clone.sh`): once the rows have been regenerated in a
clone with no `.env` and no `ANTHROPIC_API_KEY`, this decides whether they are the published
ones. It lives here, as an importable module with its own tests, rather than inside the shell
script, because the comparison is the part of the gate that can be wrong quietly -- a shell
heredoc that compares nothing at all also exits 0.

Rows are compared as canonical JSON (`indent=2, sort_keys=True`) per row number, so a
reordered key is not a difference and a changed number is.

`--tolerance` is the other half (PRD M6b, training plan C7): a retrained artefact is not
byte-identical, so its rows are compared on macro-F1 within the tolerance instead
(`make reproduce-check`, ±0.01).

Exit codes are the script's, and the script's header documents the contract:

    0  every requested row matched
    1  at least one row differed
    3  there is nothing to compare -- a requested row is absent from the committed file
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

#: Shared with `bench.artefacts`: "a prerequisite is missing, so nothing was measured".
ARTEFACT_MISSING = 3


def canonical(row: Any) -> str:
    """One row as the text the comparison is made on."""
    return json.dumps(row, indent=2, sort_keys=True)


def rows_by_number(payload: Any, wanted: Iterable[int]) -> dict[int, str]:
    """`{row number: canonical JSON}` for the wanted rows present in an ablations payload."""
    keep = set(wanted)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("an ablations payload must carry a `rows` list")
    found: dict[int, str] = {}
    for row in rows:
        number = int(row["row"])
        if number in keep:
            found[number] = canonical(row)
    return found


def load_rows(path: Path, wanted: Iterable[int]) -> dict[int, str]:
    with path.open(encoding="utf-8") as fh:
        return rows_by_number(json.load(fh), wanted)


def differences(
    committed: dict[int, str], regenerated: dict[int, str], wanted: Iterable[int]
) -> list[int]:
    """Row numbers that did not reproduce. An absent regenerated row counts as a difference."""
    return [n for n in sorted(wanted) if committed.get(n) != regenerated.get(n)]


def report(
    committed: dict[int, str], regenerated: dict[int, str], wanted: Iterable[int]
) -> tuple[int, list[str]]:
    """`(exit code, lines to print)`. Pure, so the whole decision is testable without a clone."""
    keep = sorted(set(wanted))
    absent = [n for n in keep if n not in committed]
    if absent:
        return ARTEFACT_MISSING, [
            f"SKIP: the committed file has no row(s) {absent}; there is nothing to compare"
        ]
    bad = differences(committed, regenerated, keep)
    if not bad:
        return 0, [f"OK: rows {keep} reproduced byte-for-byte from a clean clone"]
    lines = [f"FAIL: rows {bad} did not reproduce"]
    for number in bad:
        lines.append(f"--- row {number}: committed")
        lines.append(committed[number])
        lines.append(f"+++ row {number}: regenerated")
        lines.append(regenerated.get(number, "<absent>"))
    return 1, lines


#: PRD M6b: a retrain reproduces the published out-of-fold macro-F1 within this.
M6B_TOLERANCE = 0.01
TOLERANCE_KEY = "macro_f1_mean"


def tolerance_report(
    committed: dict[int, str],
    regenerated: dict[int, str],
    wanted: Iterable[int],
    tolerance: float = M6B_TOLERANCE,
) -> tuple[int, list[str]]:
    """`(exit code, lines)` for M6b: each row's `macro_f1_mean` within `tolerance`."""
    keep = sorted(set(wanted))
    absent = [n for n in keep if n not in committed]
    if absent:
        return ARTEFACT_MISSING, [
            f"SKIP: the committed file has no row(s) {absent}; there is nothing to compare"
        ]
    lines: list[str] = []
    bad: list[int] = []
    for number in keep:
        old = json.loads(committed[number]).get(TOLERANCE_KEY)
        new = json.loads(regenerated[number]).get(TOLERANCE_KEY) if number in regenerated else None
        if old is None:
            # A deferred or unscored row has no macro-F1 to reproduce; say so rather than pass it.
            lines.append(f"row {number}: no committed {TOLERANCE_KEY}; not compared")
            continue
        if new is None or abs(float(new) - float(old)) > tolerance:
            bad.append(number)
        shown = "absent" if new is None else f"{float(new):.4f}"
        lines.append(f"row {number}: committed {float(old):.4f}, regenerated {shown}")
    if bad:
        return 1, [f"FAIL: rows {bad} are outside ±{tolerance} macro-F1 (M6b)", *lines]
    if all("not compared" in line for line in lines):
        # Nothing had a number to reproduce: a skip, never a vacuous pass (Z2 review).
        return ARTEFACT_MISSING, ["SKIP: no requested row has a committed macro-F1", *lines]
    return 0, [f"OK: rows {keep} within ±{tolerance} macro-F1 (M6b)", *lines]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.reproduce")
    parser.add_argument("--committed", type=Path, required=True)
    parser.add_argument("--regenerated", type=Path, required=True)
    parser.add_argument("--rows", required=True, help="comma-separated row numbers")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="compare macro_f1_mean within this (M6b) instead of byte for byte",
    )
    ns = parser.parse_args(argv)

    wanted = [int(x) for x in ns.rows.split(",")]
    committed, regenerated = load_rows(ns.committed, wanted), load_rows(ns.regenerated, wanted)
    if ns.tolerance is None:
        code, lines = report(committed, regenerated, wanted)
    else:
        code, lines = tolerance_report(committed, regenerated, wanted, ns.tolerance)
    for line in lines:
        print(line)  # noqa: T201 -- this is a CLI; the shell script reads its output
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
