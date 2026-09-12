"""`evaluate()` and the `python -m bench.baseline.run` CLI (PRD §10.3, lane brief Task 2.5).

A record is "flagged" iff the adapter's `scan()` returns at least one finding whose
`raw["tool_name"]` matches the record's tool name. Flagged is then a hard score (1.0 / 0.0 at
threshold 0.5), so every `sieve.eval.metrics` function written for a continuous classifier score
works unchanged for a scanner's binary yes/no. All confidence intervals use
`cluster_bootstrap_ci` grouped by `server_name`, never the row-level `bootstrap_ci` (PRD §4.3):
rows from one server share an author and a register, so resampling rows alone understates the
interval.

Known limitation, for whoever builds the full corpus in Phase 2: `scan()` is called ONCE for the
whole record list (so SC-2's invocation cap counts scans, not tools) and results are joined back
to records by tool name. If two records in the same evaluation batch ever share a tool name (e.g.
a synthetic mutation that keeps its host's tool name), the join can misattribute findings between
them. `fixtures/corpus/mini-50.jsonl` has no such collisions. The fix, if it is ever needed, is to
carry the record `id` through `ScannerFinding.raw` instead of relying on `tool_name` alone — a
contracts-adjacent change to how adapters are invoked, out of this lane's scope to make unasked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, PrivateAttr

from sieve.adapters.base import ScannerAdapter, get_adapter
from sieve.eval.metrics import binary_counts, cluster_bootstrap_ci, precision_recall_fpr
from sieve.types import LABELS, CorpusRecord, ScannerName

_RESOLVABLE_SCANNERS: tuple[ScannerName, ...] = ("cisco_yara", "cisco_llm", "snyk_agent_scan")


class FpCause(BaseModel):
    """One rule id's contribution to the false-positive taxonomy (PRD §10.3)."""

    count: int
    examples: list[str]


class BaselineResult(BaseModel):
    scanner: str
    version: str
    n: int
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    fpr: float
    f1: float
    ci: dict[str, tuple[float, float]]  # cluster bootstrap by server_name
    per_tier: dict[str, dict[str, float]]
    per_concealment: dict[str, dict[str, float]]
    per_sub_label: dict[str, dict[str, float]]
    per_source: dict[str, dict[str, float]]
    confusion: list[list[int]]
    field_misses: dict[str, int]
    field_misses_approx: dict[str, int]
    fp_causes: dict[str, FpCause]

    # Per-record flag dump for `baseline_raw.json` (PRD §10.3). Not part of the published
    # aggregate schema above, so it is excluded from `model_dump()`/`baseline.json`.
    _raw_rows: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    @property
    def raw_rows(self) -> list[dict[str, Any]]:
        return self._raw_rows


@dataclass
class _Row:
    record: CorpusRecord
    flagged: bool
    rule_ids: list[str]


def _join_findings(records: list[CorpusRecord], adapter: ScannerAdapter) -> list[_Row]:
    tool_records = [r.to_tool_record() for r in records]
    findings = adapter.scan(tool_records)
    by_tool: dict[str, list[Any]] = defaultdict(list)
    for f in findings:
        tool_name = f.raw.get("tool_name")
        if tool_name is not None:
            by_tool[tool_name].append(f)

    rows: list[_Row] = []
    for r in records:
        matched = by_tool.get(r.tool.name, [])
        rule_ids = [f.rule_id for f in matched if f.rule_id]
        rows.append(_Row(record=r, flagged=len(matched) > 0, rule_ids=rule_ids))
    return rows


def _slice_metrics(rows: list[_Row]) -> dict[str, float]:
    y_true = [r.record.label for r in rows]
    y_score = [1.0 if r.flagged else 0.0 for r in rows]
    precision, recall, fpr = precision_recall_fpr(y_true, y_score, 0.5)
    return {"precision": precision, "recall": recall, "fpr": fpr, "n": float(len(rows))}


def _breakdown_by(
    rows: list[_Row], key_fn: Callable[[_Row], Iterable[str]]
) -> dict[str, dict[str, float]]:
    """Bucket rows by every value `key_fn` yields (a row may land in more than one bucket)."""
    buckets: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        for value in key_fn(r):
            buckets[value].append(r)
    return {value: _slice_metrics(subset) for value, subset in sorted(buckets.items())}


def _field_misses(rows: list[_Row], fidelity: str) -> dict[str, int]:
    """M7: poisoned, unflagged rows at this `schema_fidelity`, counted by `payload_fields`."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        rec = r.record
        if rec.label == "poisoned" and rec.schema_fidelity == fidelity and not r.flagged:
            for field in rec.payload_fields:
                counts[field] += 1
    return dict(counts)


def _fp_causes(rows: list[_Row]) -> dict[str, FpCause]:
    """Every non-poisoned flagged row, bucketed by triggering rule id, 3 examples each."""
    hits: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.record.label != "poisoned" and r.flagged:
            example = r.record.tool.description or r.record.tool.name
            for rule_id in r.rule_ids:
                hits[rule_id].append(example)
    return {
        rule_id: FpCause(count=len(examples), examples=examples[:3])
        for rule_id, examples in sorted(hits.items())
    }


def evaluate(
    records: list[CorpusRecord], adapter: ScannerAdapter, *, seed: int = 0
) -> BaselineResult:
    """Run `adapter` over `records`; a record is flagged iff the adapter returns >= 1 finding."""
    rows = _join_findings(records, adapter)
    y_true = [r.record.label for r in rows]
    y_score = [1.0 if r.flagged else 0.0 for r in rows]
    groups = [r.record.server_name for r in rows]

    tp, fp, fn, tn = binary_counts(y_true, y_score, 0.5)
    precision, recall, fpr = precision_recall_fpr(y_true, y_score, 0.5)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    def _fpr_stat(yt: Any, ys: Any) -> float:
        return precision_recall_fpr(list(yt), list(ys), 0.5)[2]

    def _recall_stat(yt: Any, ys: Any) -> float:
        return precision_recall_fpr(list(yt), list(ys), 0.5)[1]

    _, fpr_lo, fpr_hi = cluster_bootstrap_ci(_fpr_stat, y_true, y_score, groups, seed=seed)
    _, recall_lo, recall_hi = cluster_bootstrap_ci(_recall_stat, y_true, y_score, groups, seed=seed)

    label_index = {lab: i for i, lab in enumerate(LABELS)}
    confusion = [[0, 0] for _ in LABELS]  # cols: [not_flagged, flagged]
    for r in rows:
        confusion[label_index[r.record.label]][1 if r.flagged else 0] += 1

    per_tier = _breakdown_by(
        [r for r in rows if r.record.difficulty is not None],
        lambda r: [r.record.difficulty] if r.record.difficulty else [],
    )
    per_concealment = _breakdown_by(rows, lambda r: [r.record.concealment])
    per_source = _breakdown_by(rows, lambda r: [r.record.source])
    per_sub_label = _breakdown_by(rows, lambda r: list(r.record.sub_labels))

    result = BaselineResult(
        scanner=adapter.name,
        version=adapter.version,
        n=len(rows),
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=precision,
        recall=recall,
        fpr=fpr,
        f1=f1,
        ci={
            "fpr": (float(fpr_lo), float(fpr_hi)),
            "recall": (float(recall_lo), float(recall_hi)),
        },
        per_tier=per_tier,
        per_concealment=per_concealment,
        per_sub_label=per_sub_label,
        per_source=per_source,
        confusion=confusion,
        field_misses=_field_misses(rows, "exact"),
        field_misses_approx=_field_misses(rows, "approx"),
        fp_causes=_fp_causes(rows),
    )
    result._raw_rows = [
        {
            "id": r.record.id,
            "server_name": r.record.server_name,
            "label": r.record.label,
            "source": r.record.source,
            "flagged": r.flagged,
            "rule_ids": r.rule_ids,
        }
        for r in rows
    ]
    return result


def _load_corpus(path: Path) -> list[CorpusRecord]:
    if not path.exists():
        raise SystemExit(
            f"corpus file not found: {path}\n"
            "This is expected until Phase 2's human labelling lands `bench/data/labelled.jsonl`. "
            "Point --corpus at fixtures/corpus/mini-50.jsonl to exercise the harness meanwhile."
        )
    records: list[CorpusRecord] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(CorpusRecord(**json.loads(line)))
            except Exception as exc:  # noqa: BLE001 (re-raised with file/line context)
                raise SystemExit(f"{path}:{line_no}: invalid corpus record: {exc}") from exc
    return records


def _filter_split(records: list[CorpusRecord], split: str) -> list[CorpusRecord]:
    if split == "all":
        return records
    return [r for r in records if r.split == split]


def _check_holdout_gate(split: str) -> None:
    if split == "holdout" and os.environ.get("SIEVE_FINAL") != "1":
        raise SystemExit(
            "refusing --split holdout without SIEVE_FINAL=1: the holdout split and the "
            "real_poisoned slice are opened exactly once, by bench/eval_final.py (PRD §9.7, §11.6)"
        )


def _resolve_scanner_name(name: str) -> ScannerName:
    if name not in _RESOLVABLE_SCANNERS:
        raise SystemExit(f"unknown scanner {name!r}; choose from {', '.join(_RESOLVABLE_SCANNERS)}")
    return cast(ScannerName, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.baseline.run")
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--scanners", type=str, default="cisco_yara")
    parser.add_argument("--split", type=str, default="dev", choices=["dev", "holdout", "all"])
    args = parser.parse_args(argv)

    _check_holdout_gate(args.split)
    records = _filter_split(_load_corpus(args.corpus), args.split)

    scanner_names = [
        _resolve_scanner_name(s.strip()) for s in args.scanners.split(",") if s.strip()
    ]
    results = [evaluate(records, get_adapter(name)) for name in scanner_names]

    from bench.baseline import report

    report.write(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
