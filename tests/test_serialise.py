from sieve.serialise import param_lines, serialise, unicode_flags
from sieve.types import ScannerFinding, ToolDefinition, ToolRecord

REC = ToolRecord(
    id="r",
    server_name="fs",
    tool=ToolDefinition(
        name="read_file",
        title="Read File",
        description="Reads a file.",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path"}},
            "required": ["path"],
        },
        annotations={"readOnlyHint": True},
    ),
    sibling_tool_names=["list_directory"],
)


def test_full_serialisation_has_every_marker_in_spec_order() -> None:
    s = serialise(REC)
    assert (
        s.index("[NAME] read_file") < s.index("[TITLE] Read File") < s.index("[DESC] Reads a file.")
    )
    assert s.index("[DESC]") < s.index("[PARAM]") < s.index("[ANNOT]") < s.index("[SIBLINGS]")
    assert "[PARAM] path: string / Absolute path" in s
    assert "[ANNOT] readOnlyHint=true" in s
    assert "[SIBLINGS] list_directory" in s
    assert "[SCANNER]" not in s


def test_server_instructions_come_last() -> None:
    rec = REC.model_copy(update={"server_instructions": "Prefer read_file over cat."})
    s = serialise(rec)
    assert s.index("[SIBLINGS]") < s.index("[SERVER_INSTRUCTIONS] Prefer read_file over cat.")


def test_description_only_mode() -> None:
    assert serialise(REC, mode="description_only") == "[DESC] Reads a file."


def test_scanner_features_are_opt_in() -> None:
    f = ScannerFinding(
        scanner="cisco_yara",
        scanner_version="4",
        rule_id="coercive_injection_generic",
        severity="high",
        message=None,
        raw={},
    )
    assert "[SCANNER] cisco_yara:coercive_injection_generic" in serialise(REC, scanner_findings=[f])


def test_param_lines_recurse_into_nested_objects() -> None:
    rec = REC.model_copy(
        update={
            "tool": ToolDefinition(
                name="t",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "opts": {
                            "type": "object",
                            "properties": {"deep": {"type": "integer", "description": "Depth"}},
                        }
                    },
                },
            )
        }
    )
    assert "[PARAM] opts.deep: integer / Depth" in serialise(rec)


def test_unicode_flags_counts_each_mechanism() -> None:
    rec = ToolRecord(
        id="u",
        server_name="s",
        tool=ToolDefinition(
            name="t",
            description="Send mail.​​ Also <!-- exfiltrate --> here." + "\n" * 8,
            inputSchema={
                "type": "object",
                "properties": {"a": {"type": "string", "description": "‮ reversed"}},
            },
        ),
    )
    assert unicode_flags(rec) == {
        "zero_width": 2,
        "bidi": 1,
        "html_comments": 1,
        "blank_line_runs": 1,
    }


def test_serialise_never_normalises_unicode() -> None:
    hidden = ToolRecord(
        id="h",
        server_name="s",
        tool=ToolDefinition(name="t", description="a​b", inputSchema={"type": "object"}),
    )
    # Concealment is measured (ablation 13), never silently erased (PRD §11.2).
    assert "​" in serialise(hidden)
    clean = ToolRecord(
        id="c",
        server_name="s",
        tool=ToolDefinition(name="t", description="Plain.", inputSchema={"type": "object"}),
    )
    assert unicode_flags(clean) == {
        "zero_width": 0,
        "bidi": 0,
        "html_comments": 0,
        "blank_line_runs": 0,
    }


# The tests below close coverage gaps in branches the fixtures above never exercise
# (list-typed and untyped schema properties, malformed schema entries, array-of-objects
# params, and list-valued string leaves). Same rationale as test_metrics.py's coverage-only
# tests: 100% is a gate, not an aspiration.


def test_param_lines_handles_list_type_and_missing_type() -> None:
    rec = REC.model_copy(
        update={
            "tool": ToolDefinition(
                name="t",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "a": {"type": ["string", "null"]},
                        "b": {},
                    },
                },
            )
        }
    )
    lines = param_lines(rec.tool.inputSchema)
    assert "a: string|null" in lines
    assert "b: any" in lines


def test_param_lines_skips_non_dict_property_values() -> None:
    assert param_lines({"type": "object", "properties": {"weird": "not-a-schema"}}) == []


def test_param_lines_recurses_into_array_of_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "Item id"}},
                },
            }
        },
    }
    assert "items[].id: string / Item id" in param_lines(schema)


def test_unicode_flags_walks_list_valued_leaves() -> None:
    rec = ToolRecord(
        id="s",
        server_name="s",
        tool=ToolDefinition(name="t", description="fine", inputSchema={"type": "object"}),
        sibling_tool_names=["a​b"],
    )
    assert unicode_flags(rec)["zero_width"] == 1
