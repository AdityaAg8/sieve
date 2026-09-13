"""Recover MCP tool definitions from Python source with `ast` — parse only, never execute.

PRD SC-1: reading a stdio server's tools by running it executes its command, so the
default capture path is static. Two shapes are recognised:

* FastMCP `@mcp.tool()` decorated functions — the schema is *reconstructed* from the
  parameter annotations, so its fidelity is `approx`.
* Low-level `types.Tool(name=..., inputSchema={...})` literals — the schema is the
  author's own, so its fidelity is `exact`. A non-literal schema is skipped, not guessed.
"""

from __future__ import annotations

import ast
import logging
from typing import Any

from bench.collect import ExtractedTool
from sieve.types import ToolDefinition

log = logging.getLogger(__name__)

_SCALARS = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
_SEQUENCES = {"list", "tuple", "set", "Sequence", "Iterable"}


def _annotation_schema(node: ast.expr | None) -> dict[str, Any]:
    """Map a parameter annotation onto a JSON Schema fragment; `{}` when unmappable."""
    if node is None:
        return {}
    if isinstance(node, ast.Name) and node.id in _SCALARS:
        return {"type": _SCALARS[node.id]}
    if isinstance(node, ast.Name) and node.id in _SEQUENCES:
        return {"type": "array"}
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Name) and base.id in _SEQUENCES:
            return {"type": "array"}
    return {}


def _decorator_is_tool(node: ast.expr) -> ast.Call | None:
    """A decorator whose attribute chain ends in `tool` marks a tool (`@mcp.tool()`)."""
    call = node if isinstance(node, ast.Call) else None
    target = call.func if call is not None else node
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
    if name != "tool":
        return None
    # `@mcp.tool` (bare) carries no kwargs; normalise it to an empty call.
    return call if call is not None else ast.Call(func=target, args=[], keywords=[])


def _kwarg(call: ast.Call, name: str) -> Any | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError):
                return None
    return None


def _first_paragraph(docstring: str | None) -> str | None:
    if not docstring:
        return None
    paragraph = docstring.strip().split("\n\n", 1)[0]
    return " ".join(paragraph.split()) or None


def _from_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ExtractedTool | None:
    call = next(filter(None, (_decorator_is_tool(d) for d in node.decorator_list)), None)
    if call is None:
        return None
    args = node.args
    defaults: dict[str, bool] = {}
    positional = args.posonlyargs + args.args
    for offset, arg in enumerate(positional):
        defaults[arg.arg] = offset >= len(positional) - len(args.defaults)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        defaults[arg.arg] = default is not None
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg in positional + args.kwonlyargs:
        if arg.arg in {"self", "cls", "ctx", "context"}:
            continue
        properties[arg.arg] = _annotation_schema(arg.annotation)
        if not defaults.get(arg.arg, False):
            required.append(arg.arg)
    tool = ToolDefinition(
        name=str(_kwarg(call, "name") or node.name),
        title=_kwarg(call, "title"),
        description=_kwarg(call, "description") or _first_paragraph(ast.get_docstring(node)),
        inputSchema={"type": "object", "properties": properties, "required": required},
        annotations=_kwarg(call, "annotations"),
    )
    return ExtractedTool(tool=tool, schema_fidelity="approx", origin_line=node.lineno)


def _is_tool_constructor(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "Tool"


def _from_literal_call(call: ast.Call) -> ExtractedTool | None:
    name = _kwarg(call, "name")
    schema = _kwarg(call, "inputSchema")
    if not isinstance(name, str):
        log.info("skipping Tool(...) at line %d: name is not a literal", call.lineno)
        return None
    if not isinstance(schema, dict):
        log.info(
            "skipping Tool(name=%r) at line %d: inputSchema is not a literal", name, call.lineno
        )
        return None
    tool = ToolDefinition(
        name=name,
        title=_kwarg(call, "title"),
        description=_kwarg(call, "description"),
        inputSchema=schema,
        outputSchema=_kwarg(call, "outputSchema"),
        annotations=_kwarg(call, "annotations"),
    )
    return ExtractedTool(tool=tool, schema_fidelity="exact", origin_line=call.lineno)


def extract_python(source: str, *, server_name: str) -> list[ExtractedTool]:
    """Extract every tool definition in `source`. Never imports or runs the module."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        log.info("cannot parse source for %s: %s", server_name, exc)
        return []
    out: list[ExtractedTool] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            extracted = _from_function(node)
            if extracted is not None:
                out.append(extracted)
        elif isinstance(node, ast.Call) and _is_tool_constructor(node):
            extracted = _from_literal_call(node)
            if extracted is not None:
                out.append(extracted)
    out.sort(key=lambda e: e.origin_line)
    return out
