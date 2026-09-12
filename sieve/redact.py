"""Conservative redaction of credential-shaped strings (PRD SC-5). Paths are not secrets."""

from __future__ import annotations

import re
from typing import Any

from sieve.types import ToolDefinition

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    (
        "generic_assignment",
        re.compile(
            r"(?i)\b(api[_\-]?key|secret|token|password|passwd)\b"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})['\"]?"
        ),
    ),
]


def redact(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    out = text
    for kind, pat in _PATTERNS:
        if kind == "generic_assignment":
            new, n = pat.subn(lambda m: f"{m.group(1)}=[REDACTED:generic_assignment]", out)
        else:
            new, n = pat.subn(f"[REDACTED:{kind}]", out)
        if n:
            kinds.append(kind)
        out = new
    return out, kinds


def _walk(obj: Any, kinds: list[str]) -> Any:
    if isinstance(obj, str):
        red, k = redact(obj)
        kinds.extend(k)
        return red
    if isinstance(obj, dict):
        return {key: _walk(v, kinds) for key, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, kinds) for v in obj]
    return obj


def redact_tool(tool: ToolDefinition) -> tuple[ToolDefinition, list[str]]:
    """Redact every string leaf except `name`.

    `name` is an identifier joined on everywhere (findings, goldens, SARIF, the corpus) and
    must match TOOL_NAME_RE, so a redaction there would both break joins and fail validation.
    """
    kinds: list[str] = []
    data = tool.model_dump()
    name = data.pop("name")
    data = _walk(data, kinds)
    data["name"] = name
    return ToolDefinition.model_validate(data), sorted(set(kinds))
