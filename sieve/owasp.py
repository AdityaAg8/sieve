"""OWASP MCP Top 10 (2025) mapping. One table, updated in one place when the list revises."""

from __future__ import annotations

from collections.abc import Iterable

from sieve.types import SubLabel

SUB_LABEL_TO_OWASP: dict[SubLabel, str] = {
    "exfiltration": "MCP10",
    "instruction_override": "MCP06",
    "tool_shadowing": "MCP03",
    "privilege_escalation": "MCP02",
    "concealment": "MCP03",
    "secret_solicitation": "MCP01",
    "command_execution": "MCP05",
}

# Verified against https://owasp.org/www-project-mcp-top-10/ (2026-09-12).
OWASP_TITLES: dict[str, str] = {
    "MCP01": "Token Mismanagement & Secret Exposure",
    "MCP02": "Privilege Escalation via Scope Creep",
    "MCP03": "Tool Poisoning",
    "MCP05": "Command Injection & Execution",
    "MCP06": "Intent Flow Subversion",
    "MCP10": "Context Injection & Over-Sharing",
}


def owasp_for(sub_labels: Iterable[SubLabel]) -> list[str]:
    return sorted({SUB_LABEL_TO_OWASP[s] for s in sub_labels})
