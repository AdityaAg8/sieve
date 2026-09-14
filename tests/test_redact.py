from sieve.redact import redact, redact_tool
from sieve.types import ToolDefinition


def test_aws_key() -> None:
    out, kinds = redact("use AKIAIOSFODNN7EXAMPLE now")
    assert out == "use [REDACTED:aws_access_key] now" and kinds == ["aws_access_key"]


def test_private_key_block() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    out, kinds = redact(text)
    assert out == "[REDACTED:private_key]" and kinds == ["private_key"]


def test_generic_assignment() -> None:
    out, _ = redact("api_key = 'abcdefghijklmnop1234'")
    assert "abcdefghijklmnop1234" not in out


def test_paths_are_not_secrets() -> None:
    out, kinds = redact("read ~/.aws/credentials and ~/.ssh/id_rsa")
    assert out == "read ~/.aws/credentials and ~/.ssh/id_rsa" and kinds == []


def test_redact_tool_walks_schema() -> None:
    td = ToolDefinition(
        name="t",
        description="token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        inputSchema={
            "type": "object",
            "properties": {"k": {"description": "AKIAIOSFODNN7EXAMPLE"}},
        },
    )
    red, kinds = redact_tool(td)
    assert "ghp_" not in (red.description or "")
    assert "AKIA" not in red.inputSchema["properties"]["k"]["description"]
    assert sorted(kinds) == ["aws_access_key", "github_token"]


def test_redact_tool_never_rewrites_the_name() -> None:
    # A redacted name would fail TOOL_NAME_RE and break every downstream join.
    td = ToolDefinition(name="secret_token_tool", inputSchema={"type": "object"})
    red, kinds = redact_tool(td)
    assert red.name == "secret_token_tool" and kinds == []


# ------------------------------------------------------- F9: credential-shaped dict keys


def test_redact_tool_redacts_credential_shaped_key_in_input_schema_properties() -> None:
    # A credential used as a JSON-schema property name must not survive into the DB/judge/SARIF.
    td = ToolDefinition(
        name="t",
        inputSchema={
            "type": "object",
            "properties": {"AKIAIOSFODNN7EXAMPLE": {"type": "string"}},
        },
    )
    red, kinds = redact_tool(td)
    props = red.inputSchema["properties"]
    assert "AKIAIOSFODNN7EXAMPLE" not in props
    assert list(props.keys()) == ["<REDACTED:key:1>"]
    assert props["<REDACTED:key:1>"] == {"type": "string"}
    assert "aws_access_key" in kinds


def test_redact_tool_redacts_credential_shaped_key_in_nested_output_schema() -> None:
    td = ToolDefinition(
        name="t",
        inputSchema={"type": "object"},
        outputSchema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {"ghp_abcdefghijklmnopqrstuvwxyz0123456789": {"type": "string"}},
                }
            },
        },
    )
    red, kinds = redact_tool(td)
    assert red.outputSchema is not None
    nested = red.outputSchema["properties"]["result"]["properties"]
    assert list(nested.keys()) == ["<REDACTED:key:1>"]
    assert "github_token" in kinds


def test_redact_tool_redacts_credential_shaped_key_in_annotations() -> None:
    td = ToolDefinition(
        name="t",
        inputSchema={"type": "object"},
        annotations={"AKIAIOSFODNN7EXAMPLE": True},
    )
    red, kinds = redact_tool(td)
    assert red.annotations == {"<REDACTED:key:1>": True}
    assert "aws_access_key" in kinds


def test_redact_tool_gives_two_credential_keys_in_one_object_distinct_placeholders() -> None:
    td = ToolDefinition(
        name="t",
        inputSchema={
            "type": "object",
            "properties": {
                "AKIAIOSFODNN7EXAMPLE": {"type": "string"},
                "AKIAIOSFODNN7EXAMPLF": {"type": "string"},
            },
        },
    )
    red, kinds = redact_tool(td)
    keys = list(red.inputSchema["properties"].keys())
    assert len(keys) == 2
    assert len(set(keys)) == 2  # no collision: the schema stays a valid object
    assert set(keys) == {"<REDACTED:key:1>", "<REDACTED:key:2>"}


def test_redact_tool_preserves_order_of_remaining_keys() -> None:
    td = ToolDefinition(
        name="t",
        inputSchema={
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "AKIAIOSFODNN7EXAMPLE": {"type": "string"},
                "z": {"type": "string"},
            },
        },
    )
    red, _ = redact_tool(td)
    assert list(red.inputSchema["properties"].keys()) == ["a", "<REDACTED:key:1>", "z"]


def test_redact_tool_key_redaction_is_idempotent() -> None:
    td = ToolDefinition(
        name="t",
        inputSchema={
            "type": "object",
            "properties": {"AKIAIOSFODNN7EXAMPLE": {"description": "AKIAIOSFODNN7EXAMPLE"}},
        },
        annotations={"AKIAIOSFODNN7EXAMPLE": True},
    )
    once, kinds_once = redact_tool(td)
    twice, kinds_twice = redact_tool(once)
    # Redacting twice equals redacting once: the second pass changes nothing further, because
    # placeholders (both key- and value-side) don't themselves match any credential pattern.
    assert once.model_dump() == twice.model_dump()
    assert kinds_once == ["aws_access_key"]
    assert kinds_twice == []
