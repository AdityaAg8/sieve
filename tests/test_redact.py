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
        inputSchema={"type": "object", "properties": {"k": {"description": "AKIAIOSFODNN7EXAMPLE"}}},
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
