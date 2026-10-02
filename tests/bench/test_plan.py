"""The mutation plan (PRD §9.4, §9.5): which real host gets which payload spec."""

from __future__ import annotations

import collections
import json
from pathlib import Path

from bench.generate.mutate import HumanGenerator, PayloadSpec, mutate
from bench.generate.plan import (
    HOST_BUDGET_CHARS,
    MAX_USES_PER_HOST,
    POISONED_SUB_LABELS,
    build_plan,
    human_plan,
)
from sieve.types import CorpusRecord, Label

HUMAN = Path("bench/generate/human_payloads.jsonl")


def _host(i: int, server: str, label: Label = "benign", desc: str | None = None) -> CorpusRecord:
    return CorpusRecord(
        id=f"cat-{i:04d}",
        source="public_repo" if label == "benign" else "wild_fp",
        source_repo="https://huggingface.co/datasets/automatelab/mcp-servers-tool-catalog",
        source_commit="rev",
        source_license="MIT",
        capture_method="published_artifact",
        schema_fidelity="exact",
        server_name=server,
        tool={
            "name": f"tool_{i}",
            "description": desc or f"Look up record number {i} and return it.",
            "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string"}}},
        },
        label=label,
        labeller="t",
        labelled_at="2026-09-21T00:00:00Z",
    )


def _hosts(servers: int = 60, per_server: int = 4) -> list[CorpusRecord]:
    out = []
    for s in range(servers):
        for k in range(per_server):
            label: Label = "imperative_benign" if k == per_server - 1 else "benign"
            out.append(_host(s * per_server + k, f"pkg-{s}", label))
    return out


def test_the_plan_has_the_requested_mix_of_labels() -> None:
    plan = build_plan(_hosts(), poisoned=90, imperative=40, benign=12, seed=1)
    counts = collections.Counter(row["label"] for row in plan)
    assert counts == {"poisoned": 90, "imperative_benign": 40, "benign": 12}


def test_every_sub_label_combination_tier_and_concealment_is_planned() -> None:
    plan = build_plan(_hosts(), poisoned=200, imperative=0, benign=0, seed=1)
    combos = {tuple(sorted(row["sub_labels"])) for row in plan}
    assert combos == {tuple(sorted(c)) for c in POISONED_SUB_LABELS}
    assert {row["difficulty"] for row in plan} == {"easy", "medium", "hard"}
    assert {row["concealment"] for row in plan} == {
        "none",
        "html_comment",
        "zero_width",
        "bidi",
        "whitespace",
    }
    assert {tuple(row["target_fields"]) for row in plan} >= {
        ("description",),
        ("inputSchema",),
        ("description", "inputSchema"),
        ("annotations",),
    }


def test_benign_and_imperative_mutations_only_take_benign_hosts() -> None:
    hosts = _hosts()
    by_id = {h.id: h for h in hosts}
    plan = build_plan(hosts, poisoned=40, imperative=40, benign=12, seed=2)
    for row in plan:
        if row["label"] != "poisoned":
            assert by_id[row["host_id"]].label == "benign"
            assert row["sub_labels"] == [] and row["difficulty"] is None
            assert row["concealment"] == "none"


def test_a_schema_target_names_a_property_the_host_already_has() -> None:
    hosts = _hosts()
    by_id = {h.id: h for h in hosts}
    plan = build_plan(hosts, poisoned=120, imperative=40, benign=12, seed=3)
    schema_rows = [row for row in plan if "inputSchema" in row["target_fields"]]
    assert schema_rows
    for row in schema_rows:
        assert row["property_name"] in by_id[row["host_id"]].tool.inputSchema["properties"]


def test_no_host_is_used_more_than_the_cap_and_hosts_spread_across_servers() -> None:
    hosts = _hosts()
    plan = build_plan(hosts, poisoned=150, imperative=60, benign=20, seed=4)
    uses = collections.Counter(row["host_id"] for row in plan)
    assert max(uses.values()) <= MAX_USES_PER_HOST
    servers = {h.id: h.server_name for h in hosts}
    assert len({servers[row["host_id"]] for row in plan}) == 60


def test_a_host_too_long_to_carry_a_payload_is_never_chosen() -> None:
    hosts = _hosts(servers=20, per_server=3)
    hosts.append(_host(999, "pkg-long", desc="word " * (HOST_BUDGET_CHARS // 5 + 10)))
    plan = build_plan(hosts, poisoned=20, imperative=10, benign=4, seed=5)
    assert "cat-0999" not in {row["host_id"] for row in plan}


def test_the_plan_is_reproducible_for_a_seed() -> None:
    first = build_plan(_hosts(), poisoned=30, imperative=10, benign=5, seed=9)
    again = build_plan(_hosts(), poisoned=30, imperative=10, benign=5, seed=9)
    assert json.dumps(first) == json.dumps(again)


def test_the_human_plan_consumes_every_hand_authored_payload_exactly_once() -> None:
    hosts = _hosts()
    by_id = {h.id: h for h in hosts}
    rows = human_plan(hosts, HUMAN, seed=6)
    entries = [json.loads(line) for line in HUMAN.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(entries)
    gen = HumanGenerator(HUMAN)
    for index, row in enumerate(rows):
        spec = PayloadSpec(**{k: v for k, v in row.items() if k != "host_id"})
        mutate(by_id[row["host_id"]], spec, gen, new_id=f"h-{index}")
    assert gen.payloads == []
