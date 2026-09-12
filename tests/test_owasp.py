from sieve.owasp import owasp_for


def test_mapping_is_sorted_and_deduped() -> None:
    assert owasp_for(["concealment", "tool_shadowing", "exfiltration"]) == ["MCP03", "MCP10"]


def test_empty() -> None:
    assert owasp_for([]) == []
