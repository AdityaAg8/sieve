"""Merge a fresh build into `corpus.jsonl` without losing a label (training plan C6).

`corpus.jsonl` is where labels live: the TUI edits it in place and the ensemble vote (F2) writes
into it. `constructed.jsonl` is where content lives: `make corpus` rebuilds it from the catalog
and the mutation plans every run. The old Makefile seeded the corpus once (`test -f || cp`), so a
regenerated synthetic payload never reached it; copying over it would have erased every label.

Per id:

* in both, same judged text: the build's row (provenance included), the corpus's labelling
  fields. "Judged text" is what a labeller saw: the tool, its sibling names and the server
  instructions; a re-pinned commit is not new text;
* in both, judged text changed, labelled by construction (`generator`): the build's row whole --
  a regenerated synthetic payload brings its own construction label;
* in both, judged text changed under any other label (a human's or the ensemble's): a
  **conflict**, because that label judged text that no longer exists;
* only in the build: added after the existing rows, in build order;
* only in the corpus: **removed**.

Conflicts and removals stop the merge with a diff and leave `corpus.jsonl` untouched.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sieve.types import CorpusRecord

#: Written by labelling (TUI, ensemble vote, adjudication); everything else is content.
LABEL_FIELDS: tuple[str, ...] = (
    "label",
    "sub_labels",
    "difficulty",
    "concealment",
    "label_rationale",
    "labeller",
    "labelled_at",
    "second_label",
    "adjudicated",
)
CONSTRUCTION = "generator"
#: Labellers that are not a person. Anything else counts as human, so an unknown name is
#: protected rather than overwritten -- the safe way to be wrong.
MACHINE_PREFIXES: tuple[str, ...] = (CONSTRUCTION, "claude-", "ensemble", "source:")


def is_human(labeller: str) -> bool:
    return not labeller.startswith(MACHINE_PREFIXES)


@dataclass
class Diff:
    added: list[str] = field(default_factory=list)
    regenerated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.removed or self.conflicts)

    def render(self) -> str:
        lines = [
            f"merge: +{len(self.added)} added, ~{len(self.regenerated)} regenerated, "
            f"-{len(self.removed)} removed, !{len(self.conflicts)} conflicts"
        ]
        lines += [f"- {i}  (in corpus.jsonl, gone from the build)" for i in self.removed]
        lines += [f"! {i}  (text changed under a non-construction label)" for i in self.conflicts]
        lines += [f"~ {i}" for i in self.regenerated]
        lines += [f"+ {i}" for i in self.added]
        return "\n".join(lines)


def _judged(record: CorpusRecord) -> dict[str, object]:
    """What a labeller saw, the only thing a label can be about."""
    return record.to_tool_record().model_dump(mode="json", exclude={"id"})


def merge(
    existing: Sequence[CorpusRecord], built: Sequence[CorpusRecord]
) -> tuple[list[CorpusRecord], Diff]:
    diff = Diff()
    by_id = {r.id: r for r in built}
    merged: list[CorpusRecord] = []
    for old in existing:
        new = by_id.get(old.id)
        if new is None:
            diff.removed.append(old.id)
            continue
        if _judged(old) == _judged(new):
            keep = {name: getattr(old, name) for name in LABEL_FIELDS}
            merged.append(new.model_copy(update=keep))
        elif old.labeller == CONSTRUCTION:
            diff.regenerated.append(old.id)
            merged.append(new)
        else:
            diff.conflicts.append(old.id)
            merged.append(old)
    seen = {r.id for r in existing}
    for new in built:
        if new.id not in seen:
            diff.added.append(new.id)
            merged.append(new)
    return merged, diff


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_atomic(path: Path, rows: Sequence[CorpusRecord]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(r.model_dump_json() + "\n" for r in rows)
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.collect.merge_corpus")
    parser.add_argument("--constructed", type=Path, default=Path("bench/data/constructed.jsonl"))
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/corpus.jsonl"))
    ns = parser.parse_args(argv)

    built = _load(ns.constructed)
    if not ns.corpus.exists():
        _write_atomic(ns.corpus, built)
        print(f"merge: seeded {ns.corpus} with {len(built)} rows")
        return 0
    merged, diff = merge(_load(ns.corpus), built)
    if not diff.ok:
        print(diff.render(), file=sys.stderr)
        print(
            f"merge stopped: {ns.corpus} is unchanged. A removed row or a label on changed text "
            "needs a decision, not an overwrite.",
            file=sys.stderr,
        )
        return 1
    _write_atomic(ns.corpus, merged)
    print(diff.render().splitlines()[0])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
