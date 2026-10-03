"""Human gold labels for test-ID: the 104 real holdout rows (training plan §4.1, F4, F3).

The sanctioned way to put human eyes on sealed rows, for labelling only. It imports no model
code (a test asserts it), never scores anything, and its labels are report-only: they never
change a training label, a threshold or a model choice (PRD §9.7, §11.6).

`prepare` copies the real holdout rows into a gitignored working file with their labeller set
to `PENDING`; the human labels that file blind in the TUI; `export` writes every row the human
labelled to `holdout_gold.jsonl` as `{id, label, sub_labels, label_rationale, labeller,
labelled_at}`. No tool text goes into the gold file. `load_gold` is how `bench/label/audit.py`
(F3) reads it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sieve.types import CorpusRecord, Label

LABELLED = Path("bench/data/labelled.jsonl")
WORK = Path("bench/data/_labelling/test_id.jsonl")
GOLD = Path("bench/data/holdout_gold.jsonl")
PENDING = "pending-human-gold"
REAL_SOURCES = ("public_repo", "wild_fp")


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(x + "\n" for x in lines), encoding="utf-8", newline="\n")


def test_id_rows(records: list[CorpusRecord]) -> list[CorpusRecord]:
    """The real rows of the holdout: test-ID. Synthetic holdout rows are not human-labelled."""
    return [r for r in records if r.split == "holdout" and r.source in REAL_SOURCES]


def prepare(labelled: Path = LABELLED, work: Path = WORK) -> int:
    """Write the working copy (unless one exists: that is a resume). Returns its row count."""
    if not work.exists():
        rows = [
            r.model_copy(update={"labeller": PENDING, "label_rationale": None})
            for r in test_id_rows(_load(labelled))
        ]
        _write(work, [r.model_dump_json() for r in rows])
    return len(_load(work))


def export(work: Path = WORK, gold: Path = GOLD) -> int:
    """Write the rows the human has labelled to the gold file. Returns how many."""
    done = [r for r in _load(work) if r.labeller != PENDING]
    _write(
        gold,
        [
            json.dumps(
                {
                    "id": r.id,
                    "label": r.label,
                    "sub_labels": list(r.sub_labels),
                    "label_rationale": r.label_rationale,
                    "labeller": r.labeller,
                    "labelled_at": r.labelled_at.isoformat(),
                },
                ensure_ascii=False,
            )
            for r in done
        ],
    )
    return len(done)


def load_gold(gold: Path = GOLD) -> dict[str, Label]:
    """id -> the human's gold label, for the test-ID report (F3)."""
    out: dict[str, Label] = {}
    for line in gold.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            out[entry["id"]] = entry["label"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.label.holdout_gold", description=__doc__)
    parser.add_argument("--labelled", type=Path, default=LABELLED)
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--labeller", default="aditya")
    args = parser.parse_args(argv)

    from bench.label.session import LabelSession
    from bench.label.tui import LabelApp

    total = prepare(args.labelled, args.work)
    session = LabelSession(
        args.work,
        labeller=args.labeller,
        blind=True,
        intent_path=args.work.with_suffix(".intent.jsonl"),
    )
    LabelApp(session).run()
    done = export(args.work, args.gold)
    print(f"test-ID gold: {done}/{total} rows labelled -> {args.gold}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
