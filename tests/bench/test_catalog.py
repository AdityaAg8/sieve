from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.collect.catalog import (
    CATALOG_REPO_URL,
    SIBLING_CAP,
    CatalogRow,
    apply_labels,
    main,
    read_catalog,
    sample,
    to_records,
)

REVISION = "a413afb7b02ac7d615ddf1e8dfcccc815893110d"
SCHEMA = json.dumps({"type": "object", "properties": {"city": {"type": "string"}}})


def _row(
    package: str = "pkg-a",
    tool: str = "get_weather",
    desc: str | None = "Get the current weather for a city.",
    schema: str | None = SCHEMA,
    info: str | None = None,
) -> CatalogRow:
    return CatalogRow(
        package=package, tool_name=tool, description=desc, input_schema=schema, server_info=info
    )


def _records(rows: list[CatalogRow], licences: dict[str, str | None], flagged=frozenset()):
    records, _ = to_records(rows, licences, flagged, revision=REVISION)
    return records


def test_only_the_sc6_licences_survive_and_the_drops_are_counted() -> None:
    rows = [
        _row("mit-pkg", "a"),
        _row("apache-pkg", "b", desc="Search the index."),
        _row("agpl-pkg", "c", desc="List the files."),
        _row("mpl-pkg", "d", desc="Read a page."),
        _row("unknown-pkg", "e", desc="Send a message."),
    ]
    licences = {
        "mit-pkg": "MIT",
        "apache-pkg": "Apache-2.0",
        "agpl-pkg": "AGPL-3.0",
        "mpl-pkg": "MPL-2.0",
        "unknown-pkg": None,
    }
    records, dropped = to_records(rows, licences, frozenset(), revision=REVISION)
    assert sorted(r.server_name for r in records) == ["apache-pkg", "mit-pkg"]
    assert dropped["licence"] == 3


def test_a_record_carries_its_provenance_and_the_licence_of_its_own_text() -> None:
    (record,) = _records([_row("@scope/server-x", "get_weather")], {"@scope/server-x": "ISC"})
    assert record.server_name == "@scope/server-x"
    assert record.source_repo == CATALOG_REPO_URL
    assert record.source_commit == REVISION
    assert record.source_license == "ISC"
    assert record.capture_method == "published_artifact"
    assert record.schema_fidelity == "exact"
    assert record.generator_model is None
    assert record.tool.inputSchema["properties"]["city"] == {"type": "string"}


def test_a_scanner_flagged_row_is_a_wild_fp_candidate_and_the_rest_are_public_repo() -> None:
    rows = [_row("p", "flagged_tool"), _row("p", "plain_tool", desc="Return the time.")]
    records = _records(rows, {"p": "MIT"}, frozenset({("p", "flagged_tool")}))
    by_name = {r.tool.name: r for r in records}
    assert by_name["flagged_tool"].source == "wild_fp"
    assert by_name["flagged_tool"].label == "imperative_benign"
    assert by_name["plain_tool"].source == "public_repo"
    assert by_name["plain_tool"].label == "benign"


def test_siblings_are_the_other_tools_of_the_same_package_capped() -> None:
    rows = [_row("big", f"tool_{i}", desc=f"Tool number {i}.") for i in range(SIBLING_CAP + 5)]
    rows.append(_row("other", "elsewhere", desc="Unrelated."))
    records = _records(rows, {"big": "MIT", "other": "MIT"})
    first = next(r for r in records if r.tool.name == "tool_0")
    assert "tool_0" not in first.sibling_tool_names
    assert "elsewhere" not in first.sibling_tool_names
    assert len(first.sibling_tool_names) == SIBLING_CAP


def test_siblings_keep_the_servers_own_tools_list_order() -> None:
    # Serving builds `[SIBLINGS]` in the order the server lists its tools; training must too.
    rows = [
        _row("p", "zeta", desc="Last letter."),
        _row("p", "alpha", desc="First letter."),
        _row("p", "mid", desc="Middle letter."),
    ]
    by_name = {r.tool.name: r for r in _records(rows, {"p": "MIT"})}
    assert by_name["alpha"].sibling_tool_names == ["zeta", "mid"]
    assert by_name["mid"].sibling_tool_names == ["zeta", "alpha"]


def test_server_instructions_come_from_the_initialize_reply() -> None:
    info = json.dumps({"name": "x", "version": "1.0", "instructions": "Call login first."})
    (record,) = _records([_row(info=info)], {"pkg-a": "MIT"})
    assert record.server_instructions == "Call login first."


def test_empty_descriptions_and_invalid_tools_are_dropped_and_counted() -> None:
    rows = [
        _row("p", "blank", desc="   "),
        _row("p", "bad name with spaces", desc="Has a bad name."),
        _row("p", "array_schema", desc="Bad schema.", schema=json.dumps({"type": "array"})),
        _row("p", "fine", desc="Fine."),
    ]
    records, dropped = to_records(rows, {"p": "MIT"}, frozenset(), revision=REVISION)
    assert [r.tool.name for r in records] == ["fine"]
    assert dropped["empty_description"] == 1
    assert dropped["invalid_tool"] == 2


def test_an_exact_duplicate_published_under_two_packages_is_kept_once() -> None:
    rows = [_row("fork-b", "same"), _row("fork-a", "same")]
    records, dropped = to_records(
        rows, {"fork-a": "MIT", "fork-b": "MIT"}, frozenset(), revision=REVISION
    )
    assert [r.server_name for r in records] == ["fork-a"]
    assert dropped["exact_duplicate"] == 1


def test_ids_are_stable_across_runs() -> None:
    rows = [_row("p", "a"), _row("p", "b", desc="Other.")]
    first = [r.id for r in _records(rows, {"p": "MIT"})]
    second = [r.id for r in _records(list(reversed(rows)), {"p": "MIT"})]
    assert sorted(first) == sorted(second)
    assert all(i.startswith("cat-") for i in first)


def test_credential_shaped_text_is_redacted_before_storage() -> None:
    desc = "Example: api_key=ABCDEFGHIJKLMNOPQRSTUVWX1234 is sent as a header."
    (record,) = _records([_row(desc=desc)], {"pkg-a": "MIT"})
    assert "ABCDEFGHIJKLMNOPQRSTUVWX1234" not in (record.tool.description or "")
    assert "[REDACTED:generic_assignment]" in (record.tool.description or "")


def test_sample_caps_each_server_and_is_reproducible_for_a_seed() -> None:
    rows = [_row("p", f"plain_{i}", desc=f"Plain {i}.") for i in range(10)]
    rows += [_row("p", f"flag_{i}", desc=f"Flag {i}.") for i in range(10)]
    flagged = frozenset(("p", f"flag_{i}") for i in range(10))
    records = _records(rows, {"p": "MIT"}, flagged)
    picked = sample(records, seed=7, per_server_public=2, per_server_flagged=3)
    assert sum(r.source == "public_repo" for r in picked) == 2
    assert sum(r.source == "wild_fp" for r in picked) == 3
    again = sample(records, seed=7, per_server_public=2, per_server_flagged=3)
    assert [r.id for r in picked] == [r.id for r in again]


def test_provisional_labels_overwrite_the_construction_label_and_name_their_labeller() -> None:
    (record,) = _records([_row()], {"pkg-a": "MIT"})
    labels = {
        record.id: {
            "label": "imperative_benign",
            "rationale": "Ordering instruction within scope.",
            "labeller": "claude-sonnet-5-provisional",
        }
    }
    (relabelled,) = apply_labels([record], labels)
    assert relabelled.label == "imperative_benign"
    assert relabelled.label_rationale == "Ordering instruction within scope."
    assert relabelled.labeller == "claude-sonnet-5-provisional"
    assert record.label == "benign"  # the input is not mutated


def _label(text: str = "Within scope.") -> dict[str, str]:
    return {"label": "benign", "rationale": text, "labeller": "claude-sonnet-5-provisional"}


def test_a_sampled_row_the_label_file_does_not_cover_is_refused_not_left_unreviewed() -> None:
    records = _records([_row("p", "a"), _row("p", "b", desc="Other.")], {"p": "MIT"})
    with pytest.raises(ValueError, match="no label"):
        apply_labels(records, {records[0].id: _label()})


def test_a_label_for_a_row_the_sample_no_longer_holds_is_refused() -> None:
    (record,) = _records([_row()], {"pkg-a": "MIT"})
    with pytest.raises(ValueError, match="name no sampled row"):
        apply_labels([record], {record.id: _label(), "cat-000000000000": _label()})


def test_tools_and_servers_are_given_together_or_not_at_all(tmp_path: Path) -> None:
    # Half a pair used to be ignored in favour of a silent download of both.
    with pytest.raises(SystemExit):
        main(["--tools", str(tmp_path / "tools.parquet"), "--out", str(tmp_path / "o.jsonl")])


def test_read_catalog_joins_each_tool_to_its_servers_initialize_reply(tmp_path: Path) -> None:
    tools = pa.table(
        {
            "server_name": ["slug-a", "slug-a"],
            "package": ["pkg-a", "pkg-a"],
            "tool_name": ["one", "two"],
            "tool_description": ["First.", None],
            "input_schema": [SCHEMA, None],
        }
    )
    servers = pa.table(
        {
            "name": ["slug-a"],
            "package": ["pkg-a"],
            "status": ["ok"],
            "server_info": [json.dumps({"name": "a", "instructions": "Be brief."})],
        }
    )
    pq.write_table(tools, tmp_path / "tools.parquet")
    pq.write_table(servers, tmp_path / "servers.parquet")
    rows = read_catalog(tmp_path / "tools.parquet", tmp_path / "servers.parquet")
    assert [(r.package, r.tool_name) for r in rows] == [("pkg-a", "one"), ("pkg-a", "two")]
    assert rows[1].description is None
    assert json.loads(rows[0].server_info or "{}")["instructions"] == "Be brief."
