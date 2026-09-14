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


def _matches_credential(text: str) -> list[str]:
    """Which `_PATTERNS` kinds fire anywhere in `text`, without rewriting it.

    Used only to decide whether a dict *key* is credential-shaped; `redact()` remains the single
    function that actually rewrites text, so value-side behaviour is unchanged by this helper.
    """
    _, kinds = redact(text)
    return kinds


class _KeyCounter:
    """Monotonically increasing id for key placeholders, shared across one `redact_tool` call.

    Numbers are handed out in first-seen (pre-order DFS) order and never reused -- including a
    number skipped by `_next_key_placeholder` because it collided with an existing key -- so
    numbering stays deterministic for a given input while every placeholder text minted in the
    call is still guaranteed unique.
    """

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value += 1
        return self._value


def _next_key_placeholder(counter: _KeyCounter, taken: set[Any]) -> str:
    """The next `<REDACTED:key:N>` placeholder that collides with none of `taken`.

    PRD SC-7: tool definitions are attacker-controlled, so a schema can plant a literal key that
    already looks like a placeholder (e.g. `<REDACTED:key:1>`) beside a genuinely
    credential-shaped one. Without this check, whichever entry `_walk` processes second would
    silently overwrite the other via plain dict assignment -- evidence suppression in a triage
    tool. `taken` must be the union of the object's original key set (covers a not-yet-visited
    literal sibling later in the same dict) and the keys already written into the rebuilt dict
    (covers a placeholder minted earlier for an earlier sibling in this same dict); the caller is
    responsible for passing both.
    """
    while True:
        candidate = f"<REDACTED:key:{counter.next()}>"
        if candidate not in taken:
            return candidate


def _walk(obj: Any, kinds: list[str], counter: _KeyCounter) -> Any:
    if isinstance(obj, str):
        red, k = redact(obj)
        kinds.extend(k)
        return red
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        original_keys = set(obj.keys())
        for key, value in obj.items():
            new_key = key
            if isinstance(key, str):
                key_kinds = _matches_credential(key)
                if key_kinds:
                    # Replaced wholesale rather than patched in place: a partial substitution
                    # could still collide with a sibling key, and the placeholder must stand on
                    # its own as a clean, valid object key.
                    kinds.extend(key_kinds)
                    new_key = _next_key_placeholder(counter, original_keys | out.keys())
            out[new_key] = _walk(value, kinds, counter)
        return out
    if isinstance(obj, list):
        return [_walk(v, kinds, counter) for v in obj]
    return obj


def redact_tool(tool: ToolDefinition) -> tuple[ToolDefinition, list[str]]:
    """Redact every string leaf except `name`, plus any credential-shaped dict key (F9).

    `name` is an identifier joined on everywhere (findings, goldens, SARIF, the corpus) and
    must match TOOL_NAME_RE, so a redaction there would both break joins and fail validation.

    A dict key that matches one of `_PATTERNS` (e.g. a JSON-schema property named after an AWS
    key) is replaced by `<REDACTED:key:N>`, `N` assigned in first-seen order over the whole call.
    The placeholder is chosen to collide with neither the object's original keys nor a
    placeholder already minted earlier in the same object (`_next_key_placeholder`), so the
    rebuilt dict never has two logical entries collapse into one -- even against an
    attacker-supplied schema that plants a literal key shaped like a placeholder (PRD SC-7).
    Values are still redacted in place as before. Already-redacted output contains no key or
    value that matches any pattern in `_PATTERNS`, so a second pass changes nothing: redaction is
    idempotent.
    """
    kinds: list[str] = []
    data = tool.model_dump()
    name = data.pop("name")
    data = _walk(data, kinds, _KeyCounter())
    data["name"] = name
    return ToolDefinition.model_validate(data), sorted(set(kinds))
