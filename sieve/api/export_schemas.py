"""Write schemas/openapi.json and per-type JSON Schemas. Run via `make schemas`."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from sieve import protocols, types
from sieve.api.main import create_app

OUT = Path("schemas")
PUBLIC: list[tuple[str, type[BaseModel]]] = [
    (name, getattr(types, name))
    for name in (
        "ToolDefinition",
        "ToolRecord",
        "ScannerFinding",
        "EvidenceSpan",
        "TriageResult",
        "ScanCreate",
        "ScanSummary",
        "Finding",
        "FindingPage",
        "FeedbackCreate",
        "ModelInfo",
        "BenchSummary",
        "CorpusRecord",
    )
] + [(name, getattr(protocols, name)) for name in ("JudgeOutput", "JudgeFailure")]


def _dump(path: Path, payload: object) -> None:
    # encoding + newline are load-bearing: CI runs `git diff --exit-code schemas/` and a CRLF
    # written by a Windows default would fail on a clean tree.
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(body, encoding="utf-8", newline="\n")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    app = create_app(stub=True)
    _dump(OUT / "openapi.json", app.openapi())
    for name, model in PUBLIC:
        _dump(OUT / f"{name}.schema.json", model.model_json_schema())
    print(f"wrote {len(PUBLIC) + 1} schema files")


if __name__ == "__main__":
    main()
