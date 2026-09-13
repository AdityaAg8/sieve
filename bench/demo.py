"""`bench/results/demo.json`: the landing page's worked example over `fixtures/tools/sample-27`.

The file has the same `{meta, summary, findings}` shape as `fixtures/verdicts/sample-27.json`,
so the web reads one shape whether the numbers are real or provisional. `meta.provisional` is
true until a published artefact exists; nothing here is a benchmark number.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sieve.protocols import Classifier, FakeClassifier, Judge
from sieve.types import ScannerFinding, ToolDefinition, ToolRecord, TriageResult

log = logging.getLogger("bench.demo")

DEMO_SCAN_ID = "demo-0000-0000-0000-000000000000"
TOOLS_FIXTURE = Path("fixtures/tools/sample-27.json")

TriageFn = Callable[..., list[TriageResult]]


def load_tool_records(path: Path) -> list[ToolRecord]:
    """`{servers, tools}` from the fixture into `ToolRecord`s, ids as `<server>/<tool>`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    server_of: dict[str, str] = {}
    for server, names in payload["servers"].items():
        for name in names:
            server_of[name] = server
    records: list[ToolRecord] = []
    for tool in payload["tools"]:
        definition = ToolDefinition.model_validate(tool)
        server = server_of.get(definition.name, "unknown")
        records.append(
            ToolRecord(
                id=f"{server}/{definition.name}",
                server_name=server,
                tool=definition,
                sibling_tool_names=[
                    n for n in payload["servers"].get(server, []) if n != definition.name
                ],
            )
        )
    return records


def _default_triage() -> TriageFn:
    """Resolved lazily: `sieve.pipeline` is L5's and need not be importable to load this module."""
    from sieve.pipeline import triage_many

    return triage_many


def _summary(
    records: Sequence[ToolRecord],
    results: Sequence[TriageResult],
    findings_by_id: Mapping[str, list[ScannerFinding]],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    counts = {"confirmed": 0, "needs_review": 0, "dismissed": 0}
    for result in results:
        counts[result.verdict] += 1
    judge_calls = sum(1 for r in results if r.judge_model is not None)
    return {
        "id": DEMO_SCAN_ID,
        "status": "complete",
        "source_type": "tools_json",
        "tool_count": len(records),
        "scanner_flagged": sum(1 for r in records if findings_by_id.get(r.id)),
        "confirmed": counts["confirmed"],
        "needs_review": counts["needs_review"],
        "dismissed": counts["dismissed"],
        "judge_calls": judge_calls,
        "judge_cost_usd": 0.0,
        "model_version": results[0].model_version if results else None,
        "judge_model": next((r.judge_model for r in results if r.judge_model), None),
        "created_at": generated_at.isoformat().replace("+00:00", "Z"),
        "expires_at": generated_at.isoformat().replace("+00:00", "Z"),
        "error": None,
    }


def build_demo(
    tools_path: Path = TOOLS_FIXTURE,
    *,
    classifier: Classifier | None = None,
    judge: Judge | None = None,
    triage_fn: TriageFn | None = None,
    scanner_findings: Mapping[str, list[ScannerFinding]] | None = None,
    provisional: bool = True,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Triage the fixture and return the `{meta, summary, findings}` payload."""
    from sieve.pipeline import PipelineDeps

    records = load_tool_records(tools_path)
    findings_by_id: dict[str, list[ScannerFinding]] = dict(scanner_findings or {})
    run = triage_fn if triage_fn is not None else _default_triage()
    deps = PipelineDeps(
        classifier=classifier if classifier is not None else FakeClassifier(),
        judge=judge,
        judge_enabled=judge is not None,
    )
    results = run(records, findings_by_id, deps)
    when = generated_at or datetime.now(UTC)
    return {
        "meta": {
            "provisional": provisional,
            "generated_by": "python -m bench.demo",
            "source": str(tools_path).replace("\\", "/"),
        },
        "summary": _summary(records, results, findings_by_id, generated_at=when),
        "findings": [
            {
                "id": record.id,
                "scan_id": DEMO_SCAN_ID,
                "tool": record.tool.model_dump(mode="json"),
                "server_name": record.server_name,
                "sibling_tool_names": record.sibling_tool_names,
                "result": result.model_dump(mode="json"),
            }
            for record, result in zip(records, results, strict=True)
        ],
    }


def write_demo(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m bench.demo")
    parser.add_argument("--tools", type=Path, default=TOOLS_FIXTURE)
    parser.add_argument("--results-dir", type=Path, default=Path("bench/results"))
    parser.add_argument(
        "--model-id",
        default="fixture",
        help="'fixture' (the contract FakeClassifier) or a local artefact directory",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="clear meta.provisional; only for a run against a published artefact",
    )
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    from sieve.model.infer import load_classifier

    payload = build_demo(
        ns.tools,
        classifier=load_classifier(ns.model_id),
        provisional=not ns.final,
        # Fixed instant so `make bench` is byte-identical on a clean clone (M6a).
        generated_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    path = write_demo(payload, ns.results_dir / "demo.json")
    log.info("wrote %s", path)
    return path


__all__ = ["build_demo", "load_tool_records", "main", "write_demo"]

if __name__ == "__main__":  # pragma: no cover
    main()
