"""The comparison half of the clean-clone gate, exercised without a clone.

The reproduction itself needs a trained artefact nobody has pushed yet, so the shell script
exits "artefact missing" today. That is exactly why this is here: the part of the gate that
decides *pass or fail* must be proved now, not the first time an artefact exists. A comparison
that silently compares nothing also exits 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.reproduce import ARTEFACT_MISSING, differences, load_rows, main, report, rows_by_number

ROWS = [
    {"row": 1, "name": "baseline", "fpr": 0.4},
    {"row": 2, "name": "tfidf", "fpr": 0.2},
    {"row": 3, "name": "desc", "fpr": 0.1},
    {"row": 4, "name": "full", "fpr": 0.05},
    {"row": 6, "name": "judge", "fpr": 0.01},
]
WANTED = [1, 2, 3, 4]


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def test_only_the_requested_rows_are_compared() -> None:
    found = rows_by_number({"rows": ROWS}, WANTED)
    assert sorted(found) == WANTED, "row 6 needs a judge cassette and is deliberately not here"


def test_key_order_is_not_a_difference() -> None:
    shuffled = [{"fpr": r["fpr"], "name": r["name"], "row": r["row"]} for r in ROWS]
    assert rows_by_number({"rows": ROWS}, WANTED) == rows_by_number({"rows": shuffled}, WANTED)


def test_a_changed_number_is_a_difference() -> None:
    drifted = [{**r, "fpr": 0.3} if r["row"] == 2 else r for r in ROWS]
    committed = rows_by_number({"rows": ROWS}, WANTED)
    regenerated = rows_by_number({"rows": drifted}, WANTED)
    assert differences(committed, regenerated, WANTED) == [2]


def test_a_payload_with_no_rows_list_is_an_error() -> None:
    with pytest.raises(ValueError, match="`rows` list"):
        rows_by_number({"summary": {}}, WANTED)


def test_report_passes_only_when_every_requested_row_matches() -> None:
    same = rows_by_number({"rows": ROWS}, WANTED)
    code, lines = report(same, dict(same), WANTED)
    assert code == 0
    assert lines == ["OK: rows [1, 2, 3, 4] reproduced byte-for-byte from a clean clone"]


def test_report_fails_and_prints_both_sides_when_a_row_drifts() -> None:
    committed = rows_by_number({"rows": ROWS}, WANTED)
    regenerated = rows_by_number(
        {"rows": [{**r, "fpr": 0.3} if r["row"] == 4 else r for r in ROWS]}, WANTED
    )
    code, lines = report(committed, regenerated, WANTED)
    assert code == 1
    assert lines[0] == "FAIL: rows [4] did not reproduce"
    assert any('"fpr": 0.05' in line for line in lines)
    assert any('"fpr": 0.3' in line for line in lines)


def test_a_regenerated_row_that_is_absent_is_a_failure_not_a_pass() -> None:
    committed = rows_by_number({"rows": ROWS}, WANTED)
    code, lines = report(committed, {}, WANTED)
    assert code == 1
    assert "<absent>" in "\n".join(lines)


def test_a_committed_file_missing_a_requested_row_is_a_skip_not_a_pass() -> None:
    """Nothing to compare against is "not measured", which is the third exit code."""
    committed = rows_by_number({"rows": ROWS[:2]}, WANTED)
    code, lines = report(committed, committed, WANTED)
    assert code == ARTEFACT_MISSING
    assert "nothing to compare" in lines[0]


def test_the_cli_reads_two_files_and_returns_the_same_verdicts(tmp_path: Path) -> None:
    committed = _write(tmp_path / "committed.json", ROWS)
    same = _write(tmp_path / "same.json", list(reversed(ROWS)))
    drifted = _write(
        tmp_path / "drifted.json", [{**r, "fpr": 0.9} if r["row"] == 1 else r for r in ROWS]
    )

    argv = ["--committed", str(committed), "--rows", "1,2,3,4", "--regenerated"]
    assert main([*argv, str(same)]) == 0, "row order in the file is not a difference"
    assert main([*argv, str(drifted)]) == 1


def test_a_non_ascii_row_is_read_as_utf8_and_compares_by_its_characters(tmp_path: Path) -> None:
    """`bench/ablations/run.py` writes with `ensure_ascii=True`, so the canonical form escapes.

    What must not happen is a mojibake read: two files holding the same characters have to
    compare equal, and two holding different ones have to differ. Both are asserted, because
    the first alone also passes when every file is read as the same wrong encoding.
    """
    one = _write(tmp_path / "a.json", [{"row": 1, "name": "naïve baseline"}])
    copy = _write(tmp_path / "b.json", [{"row": 1, "name": "naïve baseline"}])
    other = _write(tmp_path / "c.json", [{"row": 1, "name": "naive baseline"}])

    assert load_rows(one, [1]) == load_rows(copy, [1])
    assert load_rows(one, [1]) != load_rows(other, [1])
    assert "na\\u00efve" in load_rows(one, [1])[1], "the writer's own escaping, not ours"


# --- M6b: a retrain within ±0.01 macro-F1 (training plan C7) -------------------------------


def _f1_rows(**values: float | None) -> dict[int, str]:
    from bench.reproduce import canonical

    return {
        int(k[1:]): canonical({"row": int(k[1:]), "macro_f1_mean": v}) for k, v in values.items()
    }


def test_tolerance_passes_within_and_fails_outside_0_01() -> None:
    from bench.reproduce import tolerance_report

    committed = _f1_rows(r4=0.80, r5=0.75)
    code, lines = tolerance_report(committed, _f1_rows(r4=0.809, r5=0.741), [4, 5])
    assert code == 0 and lines[0].startswith("OK")
    code, lines = tolerance_report(committed, _f1_rows(r4=0.812, r5=0.75), [4, 5])
    assert code == 1 and "[4]" in lines[0]


def test_tolerance_counts_an_absent_regenerated_row_as_a_failure() -> None:
    from bench.reproduce import tolerance_report

    code, _ = tolerance_report(_f1_rows(r4=0.8), {}, [4])
    assert code == 1


def test_tolerance_skips_when_the_committed_row_is_absent() -> None:
    from bench.reproduce import tolerance_report

    code, _ = tolerance_report({}, _f1_rows(r4=0.8), [4])
    assert code == ARTEFACT_MISSING


def test_the_cli_takes_a_tolerance(tmp_path: Path) -> None:
    committed = _write(tmp_path / "c.json", [{"row": 4, "macro_f1_mean": 0.8, "x": 1}])
    regenerated = _write(tmp_path / "r.json", [{"row": 4, "macro_f1_mean": 0.805, "x": 2}])
    argv = ["--committed", str(committed), "--regenerated", str(regenerated), "--rows", "4"]
    assert main(argv) == 1, "byte for byte, x differs"
    assert main([*argv, "--tolerance", "0.01"]) == 0
