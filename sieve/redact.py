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


def _walk(obj: Any, kinds: list[str], key_counter: list[int]) -> Any:
    if isinstance(obj, str):
        red, k = redact(obj)
        kinds.extend(k)
        return red
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            new_key: Any = key
            if isinstance(key, str):
                _, key_kinds = redact(key)
                if key_kinds:
                    # A credential-shaped key (e.g. an AWS key used as a JSON-schema property
                    # name, F9) is replaced wholesale rather than patched in place: a partial
                    # substitution could still collide with a sibling key, and the placeholder
                    # must stay a clean, valid object key on its own. The counter is shared
                    # across the whole `redact_tool` call so two redacted keys never collide.
                    kinds.extend(key_kinds)
                    key_counter[0] += 1
                    new_key = f"<REDACTED:key:{key_counter[0]}>"
            out[new_key] = _walk(value, kinds, key_counter)
        return out
    if isinstance(obj, list):
        return [_walk(v, kinds, key_counter) for v in obj]
    return obj


def redact_tool(tool: ToolDefinition) -> tuple[ToolDefinition, list[str]]:
    """Redact every string leaf except `name`, plus any credential-shaped dict key (F9).

    `name` is an identifier joined on everywhere (findings, goldens, SARIF, the corpus) and
    must match TOOL_NAME_RE, so a redaction there would both break joins and fail validation.

    A dict key that matches one of `_PATTERNS` (e.g. a JSON-schema property named after an AWS
    key) is replaced by `<REDACTED:key:N>`, numbered in first-seen order over the whole call so
    two redacted keys can never collide and the schema stays a valid object. Values are still
    redacted in place as before. Already-redacted output does not match any pattern, so a second
    pass is a no-op: redaction is idempotent.
    """
    kinds: list[str] = []
    data = tool.model_dump()
    name = data.pop("name")
    data = _walk(data, kinds, [0])
    data["name"] = name
    return ToolDefinition.model_validate(data), sorted(set(kinds))
