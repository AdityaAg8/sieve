"""Writes `bench/results/{baseline.json, baseline.md, baseline_raw.json}` (PRD §10.3).

`baseline_raw.json` is the per-record flag dump, committed so `make bench` can replay it (M2,
M6a) with no scanner installed and no network. Each row also carries a `scanner` key when more
than one scanner is evaluated in one run, so the file stays unambiguous without narrowing the
documented per-record shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.baseline.run import BaselineResult

RESULTS_DIR = Path("bench/results")

_LABEL_ORDER = ("benign", "imperative_benign", "poisoned")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _markdown_for(result: BaselineResult) -> list[str]:
    lines = [f"## {result.scanner} {result.version}", ""]
    lines.append(
        f"n={result.n}  precision={result.precision:.3f}  recall={result.recall:.3f}  "
        f"fpr={result.fpr:.3f}  f1={result.f1:.3f}"
    )
    lines.append(
        f"cluster-bootstrap 95% CI (by server_name): fpr={result.ci['fpr']}  "
        f"recall={result.ci['recall']}"
    )
    lines.append("")
    lines.append("### Confusion (rows = true label, cols = [not_flagged, flagged])")
    for label, row in zip(_LABEL_ORDER, result.confusion, strict=True):
        lines.append(f"- {label} → flagged: {row[1]} / {sum(row)}")
    lines.append("")

    for title, table in (
        ("Per tier", result.per_tier),
        ("Per concealment", result.per_concealment),
        ("Per source", result.per_source),
        ("Per sub-label", result.per_sub_label),
    ):
        lines.append(f"### {title}")
        for key, metrics in table.items():
            rendered = ", ".join(f"{k}={v:.3f}" for k, v in metrics.items())
            lines.append(f"- {key}: {rendered}")
        lines.append("")

    lines.append("### M7 field-level misses (schema_fidelity = exact)")
    for field, count in sorted(result.field_misses.items()):
        lines.append(f"- {field}: {count}")
    lines.append("")
    lines.append("### M7 field-level misses (schema_fidelity = approx, stated separately)")
    for field, count in sorted(result.field_misses_approx.items()):
        lines.append(f"- {field}: {count}")
    lines.append("")

    lines.append("### FP-cause taxonomy (every non-poisoned row flagged, by triggering rule id)")
    if not result.fp_causes:
        lines.append("- none")
    for rule_id, cause in sorted(result.fp_causes.items()):
        lines.append(f"- `{rule_id}` ({cause.count}):")
        for example in cause.examples:
            lines.append(f"  - {example}")
    lines.append("")
    return lines


def write(results: list[BaselineResult], results_dir: Path | None = None) -> None:
    results_dir = RESULTS_DIR if results_dir is None else results_dir
    results_dir.mkdir(parents=True, exist_ok=True)

    _write_json(results_dir / "baseline.json", [r.model_dump(mode="json") for r in results])

    multi_scanner = len(results) > 1
    raw_rows: list[dict[str, Any]] = []
    for r in results:
        for row in r.raw_rows:
            raw_rows.append({**row, "scanner": r.scanner} if multi_scanner else dict(row))
    _write_json(results_dir / "baseline_raw.json", raw_rows)

    lines = ["# Sieve baseline report", ""]
    for r in results:
        lines.extend(_markdown_for(r))
    (results_dir / "baseline.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
