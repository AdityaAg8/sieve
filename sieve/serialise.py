"""Field-marked serialisation of a ToolRecord plus the unicode-hygiene feature vector.

Payload-bearing fields come first so truncation at 512 tokens removes context, not evidence
(PRD §11.2). `[OUTPUT]` lines render outputSchema properties: EvidenceField names the field and
the judge suite carries output-schema mimicry cases, so a serialiser that dropped it would hand
the judge a blank tool. `serialise()` performs NO unicode normalisation; `unicode_flags()` is the
ten-line deterministic detector of ablation 13 and is never fed to the encoder.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

from sieve.types import ScannerFinding, ToolRecord

MARKERS: tuple[str, ...] = (
    "[NAME]",
    "[TITLE]",
    "[DESC]",
    "[PARAM]",
    "[OUTPUT]",
    "[ANNOT]",
    "[SIBLINGS]",
    "[SERVER_INSTRUCTIONS]",
    "[SCANNER]",
)

_ZERO_WIDTH = tuple(chr(c) for c in range(0x200B, 0x2010))  # U+200B..U+200F
_BIDI = tuple(chr(c) for c in range(0x202A, 0x202F)) + tuple(chr(c) for c in range(0x2066, 0x206A))
_HTML_COMMENT = re.compile(r"<!--")
_BLANK_RUN = re.compile(r"(?:[ \t]*\n){7,}")  # a run of more than six blank lines


def _type_of(prop: dict[str, Any]) -> str:
    t = prop.get("type")
    if isinstance(t, str):
        return t
    if isinstance(t, list) and t:
        return "|".join(str(x) for x in t)
    return "any"


def param_lines(schema: dict[str, Any], *, prefix: str = "") -> list[str]:
    """`name: type / description` per property, recursing as `a.b` and `a[].b`."""
    out: list[str] = []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return out
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        path = f"{prefix}{name}"
        desc = prop.get("description")
        line = f"{path}: {_type_of(prop)}"
        if isinstance(desc, str) and desc:
            line = f"{line} / {desc}"
        out.append(line)
        if isinstance(prop.get("properties"), dict):
            out.extend(param_lines(prop, prefix=f"{path}."))
        items = prop.get("items")
        if isinstance(items, dict) and isinstance(items.get("properties"), dict):
            out.extend(param_lines(items, prefix=f"{path}[]."))
    return out


def _annot_lines(annotations: dict[str, Any] | None) -> list[str]:
    if not annotations:
        return []
    return [
        f"{k}={v}" if isinstance(v, str) else f"{k}={json.dumps(v)}" for k, v in annotations.items()
    ]


def serialise(
    record: ToolRecord,
    *,
    mode: Literal["full", "description_only"] = "full",
    scanner_findings: list[ScannerFinding] | None = None,
) -> str:
    tool = record.tool
    if mode == "description_only":
        return f"[DESC] {tool.description or ''}".rstrip()
    lines = [f"[NAME] {tool.name}"]
    if tool.title:
        lines.append(f"[TITLE] {tool.title}")
    if tool.description:
        lines.append(f"[DESC] {tool.description}")
    lines.extend(f"[PARAM] {p}" for p in param_lines(tool.inputSchema))
    if tool.outputSchema:
        lines.extend(f"[OUTPUT] {p}" for p in param_lines(tool.outputSchema))
    lines.extend(f"[ANNOT] {a}" for a in _annot_lines(tool.annotations))
    if record.sibling_tool_names:
        lines.append(f"[SIBLINGS] {', '.join(record.sibling_tool_names)}")
    if record.server_instructions:
        lines.append(f"[SERVER_INSTRUCTIONS] {record.server_instructions}")
    for f in scanner_findings or []:
        lines.append(f"[SCANNER] {f.scanner}:{f.rule_id or 'unknown'}")
    return "\n".join(lines)


def _string_leaves(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _string_leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _string_leaves(v)


def unicode_flags(record: ToolRecord) -> dict[str, int]:
    """Hidden-content counts over every string leaf of the record (ablation 13, PRD §11.5)."""
    payload: dict[str, Any] = record.tool.model_dump()
    payload["__siblings"] = record.sibling_tool_names
    payload["__server_instructions"] = record.server_instructions or ""
    zero_width = bidi = comments = blank_runs = 0
    for s in _string_leaves(payload):
        zero_width += sum(s.count(c) for c in _ZERO_WIDTH)
        bidi += sum(s.count(c) for c in _BIDI)
        comments += len(_HTML_COMMENT.findall(s))
        blank_runs += len(_BLANK_RUN.findall(s))
    return {
        "zero_width": zero_width,
        "bidi": bidi,
        "html_comments": comments,
        "blank_line_runs": blank_runs,
    }
