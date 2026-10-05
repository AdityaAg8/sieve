"""Real tools from a published `tools/list` catalog (PRD §9.4, SC-1, SC-6).

`automatelab/mcp-servers-tool-catalog` installed and ran each npm-published MCP server and
recorded its `tools/list` reply, so the schemas are exact and nothing here executes a server
(SC-1). Every row names its npm package, and that package's npm licence is the licence of the
tool text itself (§9.8): only SC-6's licences survive, and `server_name` is the package, so a
NOTICE can attribute every record. The catalog's own CC-BY-4.0 covers its curation and is
credited as `source_repo`, pinned to one revision.

`source` comes from Cisco's YARA analyzer, run locally over the pinned catalog (the findings
file names its version and date): a flagged row is a `wild_fp` candidate — a real tool a
scanner reports — and every other row is `public_repo`. The label written here is a
construction label only. `apply_labels` replaces it with a provisional one, and the blind
human pass of §9.6 replaces both before any number is published.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ValidationError

from sieve.redact import redact, redact_tool
from sieve.types import CorpusRecord, ToolDefinition

log = logging.getLogger(__name__)

CATALOG_REPO = "automatelab/mcp-servers-tool-catalog"
CATALOG_REPO_URL = f"https://huggingface.co/datasets/{CATALOG_REPO}"
CATALOG_REVISION = "a413afb7b02ac7d615ddf1e8dfcccc815893110d"
#: PRD SC-6, verbatim. MPL-2.0 is excluded on purpose (file-level copyleft).
SC6_LICENCES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})
#: A server's full tool list can run to hundreds of names; the serialiser puts `[SIBLINGS]`
#: after every payload-bearing field, so names past the first 64 only ever reach truncation.
SIBLING_CAP = 64
IMPORTED_AT = "2026-09-21T00:00:00Z"
CONSTRUCTION_LABELLER = "catalog-construction"
ID_PREFIX = "cat-"
DEFAULT_SEED = 42
DATA_DIR = Path("bench/data/catalog")


class CatalogRow(BaseModel):
    """One `tools` row of the catalog, joined to its server's `initialize` reply."""

    package: str
    tool_name: str
    description: str | None
    input_schema: str | None  # JSON text, as the catalog stores it
    server_info: str | None = None


def read_catalog(tools_path: Path, servers_path: Path) -> list[CatalogRow]:
    import pyarrow.parquet as pq  # the model extra's; only here

    info: dict[str, str | None] = {}
    for server in pq.read_table(servers_path, columns=["package", "server_info"]).to_pylist():
        info[server["package"]] = info.get(server["package"]) or server["server_info"]
    columns = ["package", "tool_name", "tool_description", "input_schema"]
    return [
        CatalogRow(
            package=t["package"],
            tool_name=t["tool_name"],
            description=t["tool_description"],
            input_schema=t["input_schema"],
            server_info=info.get(t["package"]),
        )
        for t in pq.read_table(tools_path, columns=columns).to_pylist()
    ]


def _record_id(package: str, tool_name: str) -> str:
    return ID_PREFIX + hashlib.sha256(f"{package}::{tool_name}".encode()).hexdigest()[:12]


def _instructions(server_info: str | None) -> str | None:
    try:
        value = json.loads(server_info or "{}").get("instructions")
    except (ValueError, AttributeError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return redact(value)[0]


def to_records(
    rows: Iterable[CatalogRow],
    licences: Mapping[str, str | None],
    flagged: frozenset[tuple[str, str]],
    *,
    revision: str = CATALOG_REVISION,
) -> tuple[list[CorpusRecord], collections.Counter[str]]:
    """Every row that survives SC-6, as a record. Returns the records and the drop counts."""
    rows = list(rows)
    # Siblings in the catalog's own row order, which is the server's `tools/list` order: serving
    # builds `[SIBLINGS]` in that order, so training must see the same one. Only the processing
    # below is sorted, so which fork survives and the drop counts stay deterministic.
    names: dict[str, list[str]] = collections.defaultdict(list)
    for row in rows:
        if row.tool_name not in names[row.package]:
            names[row.package].append(row.tool_name)
    rows = sorted(rows, key=lambda r: (r.package, r.tool_name))

    dropped: collections.Counter[str] = collections.Counter()
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    out: list[CorpusRecord] = []
    for row in rows:
        licence = licences.get(row.package)
        if licence not in SC6_LICENCES:
            dropped["licence"] += 1
            continue
        record_id = _record_id(row.package, row.tool_name)
        if record_id in seen_ids:
            dropped["duplicate_name"] += 1
            continue
        if not (row.description or "").strip():
            dropped["empty_description"] += 1
            continue
        try:
            schema = json.loads(row.input_schema) if row.input_schema else {"type": "object"}
            tool = ToolDefinition(
                name=row.tool_name, description=row.description, inputSchema=schema
            )
        except (ValidationError, ValueError, TypeError):
            dropped["invalid_tool"] += 1
            continue
        # Forks republish the same server under a new package name; one copy is enough, and
        # the rest would only straddle the grouped split as a leak.
        text_key = json.dumps([tool.name, tool.description, tool.inputSchema], sort_keys=True)
        if text_key in seen_text:
            dropped["exact_duplicate"] += 1
            continue
        seen_ids.add(record_id)
        seen_text.add(text_key)
        tool, _ = redact_tool(tool)  # SC-5: the last point before the text is written to disk
        is_flagged = (row.package, row.tool_name) in flagged
        out.append(
            CorpusRecord(
                id=record_id,
                source="wild_fp" if is_flagged else "public_repo",
                source_repo=CATALOG_REPO_URL,
                source_commit=revision,
                source_license=str(licence),
                capture_method="published_artifact",
                schema_fidelity="exact",
                server_name=row.package,
                tool=tool,
                sibling_tool_names=[n for n in names[row.package] if n != row.tool_name][
                    :SIBLING_CAP
                ],
                server_instructions=_instructions(row.server_info),
                label="imperative_benign" if is_flagged else "benign",
                labeller=CONSTRUCTION_LABELLER,
                labelled_at=IMPORTED_AT,
            )
        )
    return out, dropped


def sample(
    records: list[CorpusRecord], *, seed: int, per_server_public: int, per_server_flagged: int
) -> list[CorpusRecord]:
    """At most N `public_repo` and M `wild_fp` rows per server, so no one server dominates."""
    rng = np.random.default_rng(seed)
    by_server: dict[str, list[CorpusRecord]] = collections.defaultdict(list)
    for record in records:
        by_server[record.server_name].append(record)
    picked: list[CorpusRecord] = []
    for server in sorted(by_server):
        for source, cap in (("public_repo", per_server_public), ("wild_fp", per_server_flagged)):
            pool = sorted((r for r in by_server[server] if r.source == source), key=lambda r: r.id)
            picked.extend(pool[int(i)] for i in rng.permutation(len(pool))[:cap])
    return picked


def apply_labels(
    records: list[CorpusRecord], labels: Mapping[str, Mapping[str, Any]]
) -> list[CorpusRecord]:
    """Copies of `records` carrying the provisional labels in `labels`, validated.

    The label file is keyed to one exact sample (catalog revision, seed, per-server caps, and
    numpy's generator), so any difference between the two is refused: a sampled row without a
    label would enter the corpus with its unreviewed construction label, silently.
    """
    sampled = {record.id for record in records}
    unlabelled = sorted(sampled - labels.keys())
    stale = sorted(labels.keys() - sampled)
    if unlabelled or stale:
        raise ValueError(
            f"the labels do not match the sampled rows: {len(unlabelled)} sampled row(s) have "
            f"no label {unlabelled[:3]}, {len(stale)} label(s) name no sampled row {stale[:3]}; "
            "the sample changed (catalog revision, --seed, the per-server caps, or numpy)"
        )
    out: list[CorpusRecord] = []
    for record in records:
        entry = labels[record.id]
        update = {
            "label": entry["label"],
            "sub_labels": list(entry.get("sub_labels", [])),
            "label_rationale": entry.get("rationale"),
            "labeller": entry["labeller"],
            "labelled_at": entry.get("labelled_at", IMPORTED_AT),
        }
        out.append(CorpusRecord.model_validate({**record.model_dump(), **update}))
    return out


def download(revision: str = CATALOG_REVISION) -> tuple[Path, Path]:
    """Fetch the two parquet files at the pinned revision (cached by `huggingface_hub`)."""
    from huggingface_hub import hf_hub_download

    tools, servers = (
        Path(
            hf_hub_download(
                CATALOG_REPO, f"data/{name}.parquet", repo_type="dataset", revision=revision
            )
        )
        for name in ("tools", "servers")
    )
    return tools, servers


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.collect.catalog",
        description="Import real tools from the pinned tools/list catalog as corpus records.",
    )
    parser.add_argument("--tools", type=Path, default=None, help="default: download")
    parser.add_argument("--servers", type=Path, default=None, help="default: download")
    parser.add_argument("--licences", type=Path, default=DATA_DIR / "licences.json")
    parser.add_argument("--findings", type=Path, default=DATA_DIR / "wild_findings.json")
    parser.add_argument("--labels", type=Path, default=DATA_DIR / "provisional_labels.json")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--per-server-public", type=int, default=2)
    parser.add_argument("--per-server-flagged", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("bench/data/real.jsonl"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if (args.tools is None) != (args.servers is None):
        parser.error("--tools and --servers go together: give both, or neither to download")
    if args.tools is None:
        args.tools, args.servers = download()
    licences = _load_json(args.licences)["licences"]
    flagged = frozenset(
        (f["package"], f["tool_name"]) for f in _load_json(args.findings)["flagged"]
    )
    records, dropped = to_records(read_catalog(args.tools, args.servers), licences, flagged)
    picked = sample(
        records,
        seed=args.seed,
        per_server_public=args.per_server_public,
        per_server_flagged=args.per_server_flagged,
    )
    if args.labels.exists():
        try:
            picked = apply_labels(picked, _load_json(args.labels)["labels"])
        except ValueError as exc:
            raise SystemExit(f"{args.labels}: {exc}") from exc
    else:
        log.warning("no %s: keeping construction labels", args.labels)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in picked), encoding="utf-8", newline="\n"
    )
    mix = collections.Counter((r.source, r.label) for r in picked)
    print(f"kept {len(records)} of the catalog, dropped {dict(dropped)}")
    print(f"wrote {len(picked)} records to {args.out}: {dict(sorted(mix.items()))}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
