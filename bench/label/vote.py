"""The ensemble vote (training plan F2) and the synthetic dispute rule (E5), PRD-plan §4.1.

**Real dev rows.** Three votes: the Sonnet provisional label, Qwen, Gemma.
* ANY vote for `poisoned` sends the row to the human review queue (no real row is poisoned by
  source, so a poisoned vote is either an error or a finding, and only a person decides).
* Otherwise benign vs imperative_benign is the majority. Three valid votes on two classes always
  have one. §8 risk 7: with a vote lost to a parse failure, the remaining two decide if they
  agree, else the queue; with fewer than two, the queue.
* A human label (dev-50, the review queue, anything not machine-named) is never overwritten.
* Sealed rows are never relabelled: their votes only feed the report-only audits.

**Synthetic rows (E5).** The checker is the family that did not write the payload (hand-written
rows: both). A checker's valid vote that differs from the construction label is a dispute. A
disputed model-written row is regenerated ONCE (a new seed) and re-checked; still disputed, it is
dropped and counted (`payload: null` in `payloads_v1.jsonl`, retired by the next `make corpus`).
Hand-written payloads are kept verbatim, so their disputes are reported, never acted on.

Writes the labels into `corpus.jsonl`, the review-queue ids (`make review-queue` opens them
blind in the TUI), `bench/results/ensemble_agreement.json`, and every voted row's ensemble label
for the audits (F3).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.collect.merge_corpus import is_human
from bench.generate.generate_local import MODELS
from bench.label.ensemble import labellers
from bench.ollama import RawLog
from sieve.eval.metrics import cohens_kappa
from sieve.types import CorpusRecord

LABELLER = "ensemble-v1"
SONNET = "sonnet"
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})
SYNTHETIC = "synthetic_mutation"
HUMAN_GENERATOR = "human"
#: The one regeneration of a disputed row uses this seed, pre-registered (lint retries use 0-2).
DISPUTE_SEED = 10
VOTED_AT = datetime(2026, 10, 3, tzinfo=UTC)


@dataclass(frozen=True)
class Decision:
    label: str | None
    queue: str | None


@dataclass(frozen=True)
class Paths:
    corpus: Path
    provisional: Path
    log: Path
    recheck_log: Path
    payloads: Path
    state: Path
    queue: Path
    agreement: Path
    majority: Path


def decide_real(votes: Mapping[str, str | None]) -> Decision:
    """The §4.1 rule for one real row; `votes` maps voter -> label, None for no valid vote."""
    valid = [v for v in votes.values() if v is not None]
    if "poisoned" in valid:
        return Decision(None, "poisoned_vote")
    if len(valid) < 2:
        return Decision(None, "too_few_votes")
    counts = {label: valid.count(label) for label in set(valid)}
    top = max(counts.values())
    winners = [label for label, n in counts.items() if n == top]
    if len(winners) > 1:
        return Decision(None, "parse_failure_split")
    return Decision(winners[0], None)


def disputed(construction: str, checks: Mapping[str, str | None]) -> bool:
    return any(v is not None and v != construction for v in checks.values())


def load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _json(path: Path, payload: Any) -> None:
    _write_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _vote(log: RawLog, model: str, row_id: str) -> str | None:
    entry = log.get(model, row_id)
    return entry["label"] if entry and entry.get("status") == "ok" else None


def _votes(row: CorpusRecord, log: RawLog, sonnet: Mapping[str, str]) -> dict[str, str | None]:
    return {SONNET: sonnet.get(row.id), **{m: _vote(log, m, row.id) for m in MODELS.values()}}


def _checks(row: CorpusRecord, log: RawLog) -> dict[str, str | None]:
    return {m: _vote(log, m, row.id) for m in labellers(row)}


def _model_written(row: CorpusRecord) -> bool:
    return row.generator_model != HUMAN_GENERATOR and row.id.startswith("sb-syn-")


def _drop_payloads(path: Path, ids: Sequence[str]) -> None:
    indices = {int(i.rsplit("-", 1)[1]) for i in ids}
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["plan_index"] in indices:
            entry.update({"payload": None, "dropped": "checker dispute after one regeneration"})
        lines.append(json.dumps(entry, ensure_ascii=False))
    _write_atomic(path, "".join(x + "\n" for x in lines))


def run_vote(
    paths: Paths,
    *,
    regenerate: Callable[[list[str]], None],
    rebuild: Callable[[], None],
) -> int:
    """The whole vote, resumable: the dispute round is recorded in `paths.state`."""
    sonnet = {
        rid: str(entry["label"])
        for rid, entry in json.loads(paths.provisional.read_text(encoding="utf-8"))[
            "labels"
        ].items()
    }
    log = RawLog(paths.log)
    state: dict[str, Any] = (
        json.loads(paths.state.read_text(encoding="utf-8"))
        if paths.state.exists()
        else {"round": 0, "regenerated": [], "dropped": [], "human_disputed": []}
    )

    # ------------------------------------------------------------- synthetic rows (E5)
    synthetic = [r for r in load(paths.corpus) if r.source == SYNTHETIC]
    first = {r.id: _checks(r, log) for r in synthetic}
    round0 = sorted(r.id for r in synthetic if disputed(r.label, first[r.id]))
    by_id = {r.id: r for r in synthetic}
    state["human_disputed"] = [i for i in round0 if not _model_written(by_id[i])]
    if state["round"] == 0:
        state["regenerated"] = [i for i in round0 if _model_written(by_id[i])]
        state["round"] = 1
        _json(paths.state, state)
        if state["regenerated"]:
            regenerate(list(state["regenerated"]))
    recheck = RawLog(paths.recheck_log)
    current = {r.id: r for r in load(paths.corpus) if r.source == SYNTHETIC}
    still = sorted(
        i
        for i in state["regenerated"]
        if i in current and disputed(current[i].label, _checks(current[i], recheck))
    )
    if still:
        _drop_payloads(paths.payloads, still)
        state["dropped"] = sorted(set(state["dropped"]) | set(still))
        _json(paths.state, state)
        rebuild()

    # ------------------------------------------------------------------ real rows (F2)
    rows = load(paths.corpus)
    queue: list[str] = []
    majority: dict[str, str | None] = {}
    out = []
    for row in rows:
        if row.source not in REAL_SOURCES:
            out.append(row)
            continue
        votes = _votes(row, log, sonnet)
        decision = decide_real(votes)
        majority[row.id] = decision.label or (
            "poisoned" if decision.queue == "poisoned_vote" else None
        )
        if row.split != "dev" or is_human(row.labeller):
            out.append(row)  # sealed rows feed the audits only; a person's label always wins
            continue
        if decision.label is None:
            queue.append(row.id)
            out.append(row)
            continue
        shown = ", ".join(f"{k.split(':')[0]}={v}" for k, v in votes.items())
        out.append(
            row.model_copy(
                update={
                    "label": decision.label,
                    "labeller": LABELLER,
                    "labelled_at": VOTED_AT,
                    "label_rationale": f"votes: {shown}"[:300],
                }
            )
        )
    _write_atomic(paths.corpus, "".join(r.model_dump_json() + "\n" for r in out))
    _json(paths.queue, sorted(queue))
    _json(paths.majority, majority)
    construction = {r.id: r.label for r in synthetic}  # round 0: dropped rows count too
    _json(paths.agreement, _agreement(rows, log, sonnet, first, construction, state, len(queue)))
    print(
        f"vote: {len(queue)} row(s) in the review queue; {len(state['dropped'])} synthetic dropped"
    )
    return 0


def _agreement(
    rows: Sequence[CorpusRecord],
    log: RawLog,
    sonnet: Mapping[str, str],
    checks: Mapping[str, Mapping[str, str | None]],
    construction: Mapping[str, str],
    state: Mapping[str, Any],
    queue: int,
) -> dict[str, Any]:
    real = [r for r in rows if r.source in REAL_SOURCES and r.split == "dev"]
    voters = {
        "sonnet": lambda r: sonnet.get(r.id),
        "qwen": lambda r: _vote(log, MODELS["qwen"], r.id),
        "gemma": lambda r: _vote(log, MODELS["gemma"], r.id),
    }
    kappas = {}
    for a, b in (("sonnet", "qwen"), ("sonnet", "gemma"), ("qwen", "gemma")):
        pairs = [(voters[a](r), voters[b](r)) for r in real]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        kappas[f"{a}~{b}"] = {
            "kappa": round(cohens_kappa([x for x, _ in pairs], [y for _, y in pairs]), 4)
            if pairs
            else None,
            "n": len(pairs),
        }
    votes = [
        (construction[i], v)
        for i, c in checks.items()
        if i in construction
        for v in c.values()
        if v is not None
    ]
    model_rows = sum(1 for i in checks if i.startswith("sb-syn-"))
    failures = {
        m: sum(1 for r in rows if (e := log.get(m, r.id)) and e.get("status") != "ok")
        for m in MODELS.values()
    }
    return {
        "pairwise_kappa": kappas,
        "synthetic": {
            "checker_agreement_round0": round(sum(a == b for a, b in votes) / len(votes), 4)
            if votes
            else None,
            "regenerated": len(state["regenerated"]),
            "dropped": len(state["dropped"]),
            "rejection_rate": round(len(state["dropped"]) / model_rows, 4) if model_rows else None,
            "human_disputed": list(state["human_disputed"]),
        },
        "review_queue": queue,
        "parse_failures": failures,
    }


# ----------------------------------------------------------------------------------- CLI


def _regenerate(ids: list[str]) -> None:
    """The one regeneration: new payloads (seed `DISPUTE_SEED`), the corpus rebuilt, the
    rows re-checked by their checker into the recheck log."""
    python = sys.executable
    ids_file = Path("bench/data/ensemble/disputed_ids.json")
    _json(ids_file, ids)
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            python,
            "-m",
            "bench.generate.generate_local",
            "--all",
            "--only-ids-file",
            str(ids_file),
            "--seed",
            str(DISPUTE_SEED),
            "--raw-log",
            "bench/data/synthetic/raw/generate_v1_dispute.jsonl",
        ],
        check=True,
    )
    subprocess.run(["make", "corpus"], check=True)  # noqa: S603, S607 - the repo's own target
    subprocess.run(  # noqa: S603
        [
            python,
            "-m",
            "bench.label.ensemble",
            "--all",
            "--ids-file",
            str(ids_file),
            "--raw-log",
            "bench/data/ensemble/ensemble_v1_recheck.jsonl",
        ],
        check=True,
    )


def _rebuild() -> None:
    subprocess.run(["make", "corpus"], check=True)  # noqa: S603, S607 - the repo's own target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.label.vote")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/corpus.jsonl"))
    parser.add_argument(
        "--provisional", type=Path, default=Path("bench/data/catalog/provisional_labels.json")
    )
    parser.add_argument("--log", type=Path, default=Path("bench/data/ensemble/ensemble_v1.jsonl"))
    parser.add_argument(
        "--recheck-log", type=Path, default=Path("bench/data/ensemble/ensemble_v1_recheck.jsonl")
    )
    parser.add_argument(
        "--payloads", type=Path, default=Path("bench/data/synthetic/payloads_v1.jsonl")
    )
    parser.add_argument("--state", type=Path, default=Path("bench/data/ensemble/disputes_v1.json"))
    parser.add_argument(
        "--queue", type=Path, default=Path("bench/data/_labelling/review_queue_ids.json")
    )
    parser.add_argument(
        "--agreement", type=Path, default=Path("bench/results/ensemble_agreement.json")
    )
    parser.add_argument(
        "--majority", type=Path, default=Path("bench/data/ensemble/majority_v1.json")
    )
    ns = parser.parse_args(argv)
    paths = Paths(
        corpus=ns.corpus,
        provisional=ns.provisional,
        log=ns.log,
        recheck_log=ns.recheck_log,
        payloads=ns.payloads,
        state=ns.state,
        queue=ns.queue,
        agreement=ns.agreement,
        majority=ns.majority,
    )
    return run_vote(paths, regenerate=_regenerate, rebuild=_rebuild)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
