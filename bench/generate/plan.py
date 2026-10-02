"""The mutation plan (PRD §9.4, §9.5): which real host receives which payload spec.

A plan row is exactly what `bench.generate.mutate` consumes: `host_id` plus the `PayloadSpec`
fields. The poisoned rows cross every sub-label (singly and in the common pairs), the three
semantic tiers, the concealment attribute and four injection sites, so the bench can report
each of them. Hosts are real rows only. Benign and imperative mutations take `benign` hosts,
so the added sentence alone decides the class; poisoned mutations take either real class
(§9.5 step 1), which is what puts a payload inside an already-directive description.

Hosts are dealt round-robin across servers so no server carries a cluster of mutations, and a
host whose payload-bearing text is already long is skipped: a payload pushed past DeBERTa's
512 tokens is refused by `sieve.model.train` rather than trained on.
"""

from __future__ import annotations

import argparse
import collections
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from sieve.serialise import param_lines
from sieve.types import CorpusRecord, Label, SubLabel

SINGLES: tuple[tuple[SubLabel, ...], ...] = (
    ("exfiltration",),
    ("instruction_override",),
    ("tool_shadowing",),
    ("privilege_escalation",),
    ("secret_solicitation",),
    ("command_execution",),
    ("concealment",),
)
PAIRS: tuple[tuple[SubLabel, ...], ...] = (
    ("exfiltration", "concealment"),
    ("secret_solicitation", "exfiltration"),
    ("instruction_override", "concealment"),
    ("tool_shadowing", "exfiltration"),
    ("command_execution", "concealment"),
)
POISONED_SUB_LABELS: tuple[tuple[SubLabel, ...], ...] = SINGLES + PAIRS
#: Every single four times, then every pair once: singles are the bulk, pairs still recur.
_SUB_LABEL_CYCLE = SINGLES * 4 + PAIRS

TIERS = ("easy", "medium", "hard")
TIER_WEIGHTS = (0.3, 0.4, 0.3)
CONCEALMENTS = ("none", "html_comment", "zero_width", "bidi", "whitespace")
CONCEALMENT_WEIGHTS = (0.72, 0.07, 0.07, 0.07, 0.07)
POISONED_SITES: tuple[tuple[str, ...], ...] = (
    ("description",),
    ("inputSchema",),
    ("description", "inputSchema"),
    ("annotations",),
)
POISONED_SITE_WEIGHTS = (0.60, 0.28, 0.07, 0.05)
DIRECTIVE_SITES: tuple[tuple[str, ...], ...] = (("description",), ("inputSchema",))
IMPERATIVE_SITE_WEIGHTS = (0.75, 0.25)
BENIGN_SITE_WEIGHTS = (0.80, 0.20)

#: Description plus rendered parameter lines, in characters (~4 per token): room is left for a
#: payload of up to ~100 tokens, even zero-width-spaced, inside DeBERTa's 512.
HOST_BUDGET_CHARS = 1200
MAX_USES_PER_HOST = 2
DEFAULT_SEED = 42
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})


def _payload_chars(record: CorpusRecord) -> int:
    tool = record.tool
    return len(tool.description or "") + sum(len(line) for line in param_lines(tool.inputSchema))


def _first_property(record: CorpusRecord) -> str | None:
    properties = record.tool.inputSchema.get("properties") or {}
    return next(iter(properties), None) if isinstance(properties, dict) else None


class _HostPicker:
    """Deals hosts round-robin over shuffled servers, never past `MAX_USES_PER_HOST`."""

    def __init__(
        self,
        records: Sequence[CorpusRecord],
        rng: np.random.Generator,
        uses: collections.Counter[str],
    ) -> None:
        by_server: dict[str, list[CorpusRecord]] = collections.defaultdict(list)
        for record in sorted(records, key=lambda r: r.id):
            if record.source in REAL_SOURCES and _payload_chars(record) <= HOST_BUDGET_CHARS:
                by_server[record.server_name].append(record)
        names = sorted(by_server)
        self.servers = [names[int(i)] for i in rng.permutation(len(names))]
        self.by_server = {
            s: [by_server[s][int(i)] for i in rng.permutation(len(by_server[s]))]
            for s in self.servers
        }
        self.uses = uses
        self.cursor = 0

    def pick(self, labels: frozenset[str], needs_property: bool) -> CorpusRecord:
        for step in range(len(self.servers)):
            server = self.servers[(self.cursor + step) % len(self.servers)]
            for host in self.by_server[server]:
                if host.label not in labels or self.uses[host.id] >= MAX_USES_PER_HOST:
                    continue
                if needs_property and _first_property(host) is None:
                    continue
                self.uses[host.id] += 1
                self.cursor = (self.cursor + step + 1) % len(self.servers)
                return host
        raise ValueError(f"no host left for labels={sorted(labels)} property={needs_property}")


def _row(
    host: CorpusRecord,
    label: Label,
    target_fields: Sequence[str],
    *,
    sub_labels: Sequence[str] = (),
    difficulty: str | None = None,
    concealment: str = "none",
) -> dict[str, Any]:
    return {
        "host_id": host.id,
        "label": label,
        "sub_labels": list(sub_labels),
        "difficulty": difficulty,
        "target_fields": list(target_fields),
        "property_name": _first_property(host) if "inputSchema" in target_fields else None,
        "concealment": concealment,
    }


def _choice(rng: np.random.Generator, options: Sequence[Any], weights: Sequence[float]) -> Any:
    return options[int(rng.choice(len(options), p=weights))]


def build_plan(
    records: Sequence[CorpusRecord],
    *,
    poisoned: int,
    imperative: int,
    benign: int,
    seed: int = DEFAULT_SEED,
    uses: collections.Counter[str] | None = None,
) -> list[dict[str, Any]]:
    """Plan rows for a model-authored payload file: poisoned, then imperative, then benign."""
    rng = np.random.default_rng(seed)
    picker = _HostPicker(records, rng, uses if uses is not None else collections.Counter())
    any_real = frozenset({"benign", "imperative_benign"})
    benign_only = frozenset({"benign"})
    rows: list[dict[str, Any]] = []
    for index in range(poisoned):
        site = _choice(rng, POISONED_SITES, POISONED_SITE_WEIGHTS)
        host = picker.pick(any_real, needs_property="inputSchema" in site)
        rows.append(
            _row(
                host,
                "poisoned",
                site,
                sub_labels=_SUB_LABEL_CYCLE[index % len(_SUB_LABEL_CYCLE)],
                difficulty=_choice(rng, TIERS, TIER_WEIGHTS),
                concealment=_choice(rng, CONCEALMENTS, CONCEALMENT_WEIGHTS),
            )
        )
    for label, count, weights in (
        ("imperative_benign", imperative, IMPERATIVE_SITE_WEIGHTS),
        ("benign", benign, BENIGN_SITE_WEIGHTS),
    ):
        for _ in range(count):
            site = _choice(rng, DIRECTIVE_SITES, weights)
            host = picker.pick(benign_only, needs_property="inputSchema" in site)
            rows.append(_row(host, label, site))  # type: ignore[arg-type]
    return rows


def human_plan(
    records: Sequence[CorpusRecord],
    payloads: Path,
    *,
    seed: int = DEFAULT_SEED,
    uses: collections.Counter[str] | None = None,
) -> list[dict[str, Any]]:
    """One description-site row per hand-authored payload, in the file's own order."""
    rng = np.random.default_rng(seed)
    picker = _HostPicker(records, rng, uses if uses is not None else collections.Counter())
    rows: list[dict[str, Any]] = []
    for line in payloads.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        host = picker.pick(frozenset({"benign", "imperative_benign"}), needs_property=False)
        rows.append(
            _row(
                host,
                "poisoned",
                ("description",),
                sub_labels=entry["sub_labels"],
                difficulty=entry["difficulty"],
            )
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.generate.plan",
        description="Plan which real host receives which payload spec (PRD §9.4, §9.5).",
    )
    parser.add_argument("--corpus", type=Path, required=True, help="real rows (JSONL)")
    parser.add_argument(
        "--human-payloads", type=Path, default=Path("bench/generate/human_payloads.jsonl")
    )
    parser.add_argument("--poisoned", type=int, default=282)
    parser.add_argument("--imperative", type=int, default=200)
    parser.add_argument("--benign", type=int, default=60)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, required=True, help="model-authored plan (JSON)")
    parser.add_argument("--human-out", type=Path, required=True, help="hand-authored plan (JSON)")
    args = parser.parse_args(argv)

    records = [
        CorpusRecord.model_validate_json(line)
        for line in args.corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    uses: collections.Counter[str] = collections.Counter()
    human = human_plan(records, args.human_payloads, seed=args.seed, uses=uses)
    model = build_plan(
        records,
        poisoned=args.poisoned,
        imperative=args.imperative,
        benign=args.benign,
        seed=args.seed + 1,
        uses=uses,
    )
    for path, rows in ((args.human_out, human), (args.out, model)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(rows)} plan rows to {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
