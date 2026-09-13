"""The minimal-pair index (PRD §9.7).

Every `(host, mutation)` pair differs by exactly one inserted payload, so the pair is a
controlled experiment: if the model's verdict does not change between them, it is not
reading the payload. L3 computes M11 from this file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sieve.types import CorpusRecord

DEFAULT_OUT = Path("bench/results/minimal_pairs.json")


def build(records: list[CorpusRecord]) -> list[dict[str, Any]]:
    """Every pair whose host is present. A pair with a missing host is not a minimal pair."""
    known = {r.id for r in records}
    return [
        {
            "host": r.mutation_of,
            "mutation": r.id,
            "label": r.label,
            "difficulty": r.difficulty,
            "concealment": r.concealment,
        }
        for r in records
        if r.mutation_of and r.mutation_of in known
    ]


def write(records: list[CorpusRecord], out: Path = DEFAULT_OUT) -> int:
    """Write the pair index and return the pair count."""
    entries = build(records)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8", newline="\n")
    return len(entries)


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.split.pairs",
        description="Emit every (host, mutation) minimal pair so L3 can compute M11.",
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    print(f"wrote {write(_load(args.corpus), args.out)} pairs to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
