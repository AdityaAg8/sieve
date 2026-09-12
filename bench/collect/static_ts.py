"""Recover MCP tool definitions from TypeScript source with tree-sitter — parse only.

Fidelity is always `approx`: a zod shape is a *program*, and the only honest way to get
the exact JSON Schema out of it is to run the server (PRD SC-1 says not to, outside the
sandbox). So the zod object literal is read structurally — `z.string()` → string,
`z.number()` → number, `z.boolean()` → boolean, `z.array(...)` → array, `.describe("…")`
becomes the property description and `.optional()` drops it from `required`.

Verified against tree-sitter 0.26 / tree-sitter-typescript: `Language(tst.language_typescript())`
then `Parser(language)`.
"""

from __future__ import annotations

import logging
from typing import Any

import tree_sitter_typescript as tst
from tree_sitter import Language, Node, Parser

from bench.collect import ExtractedTool
from sieve.types import ToolDefinition

log = logging.getLogger(__name__)

TOOL_METHODS = frozenset({"tool", "registerTool"})
_ZOD_TYPES = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

_LANGUAGE = Language(tst.language_typescript())


def _text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


def _string_value(node: Node | None) -> str | None:
    """The literal value of a `string` node, or None when the node is not a literal."""
    if node is None or node.type not in {"string", "template_string"}:
        return None
    fragments = [_text(c) for c in node.children if c.type in {"string_fragment", "template_chars"}]
    return "".join(fragments) if fragments else ""


def _chain(node: Node) -> list[tuple[str, Node]]:
    """Flatten `z.string().describe("x").optional()` into [(method, arguments), ...]."""
    methods: list[tuple[str, Node]] = []
    current: Node | None = node
    while current is not None and current.type == "call_expression":
        func = current.child_by_field_name("function")
        args = current.child_by_field_name("arguments")
        if func is None or func.type != "member_expression" or args is None:
            break
        prop = func.child_by_field_name("property")
        if prop is not None:
            methods.append((_text(prop), args))
        current = func.child_by_field_name("object")
    methods.reverse()
    return methods


def _property_schema(value: Node) -> tuple[dict[str, Any], bool]:
    """Map one zod expression onto a schema fragment plus whether it is required."""
    schema: dict[str, Any] = {}
    required = True
    for method, args in _chain(value):
        if method in _ZOD_TYPES and "type" not in schema:
            schema["type"] = _ZOD_TYPES[method]
        elif method == "describe":
            described = next((_string_value(a) for a in args.named_children), None)
            if described is not None:
                schema["description"] = described
        elif method in {"optional", "nullish", "default"}:
            required = False
    return schema, required


def _pairs(obj: Node) -> list[tuple[str, Node]]:
    out: list[tuple[str, Node]] = []
    for child in obj.named_children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None:
            continue
        name = _string_value(key) if key.type == "string" else _text(key)
        out.append((name or _text(key), value))
    return out


def _shape_schema(shape: Node | None, *, tool_name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if shape is None or shape.type != "object":
        log.info("tool %s: input shape is not an object literal; schema left empty", tool_name)
        return {"type": "object", "properties": properties, "required": required}
    for name, value in _pairs(shape):
        schema, is_required = _property_schema(value)
        properties[name] = schema
        if is_required:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _from_register_tool(name: str, args: list[Node], line: int) -> ExtractedTool | None:
    config = next((a for a in args if a.type == "object"), None)
    if config is None:
        return None
    fields = dict(_pairs(config))
    title = _string_value(fields.get("title"))
    description = _string_value(fields.get("description"))
    schema = _shape_schema(fields.get("inputSchema"), tool_name=name)
    tool = ToolDefinition(name=name, title=title, description=description, inputSchema=schema)
    return ExtractedTool(tool=tool, schema_fidelity="approx", origin_line=line)


def _from_tool(name: str, args: list[Node], line: int) -> ExtractedTool | None:
    strings = [a for a in args if a.type == "string"]
    description = _string_value(strings[1]) if len(strings) > 1 else None
    schema = _shape_schema(next((a for a in args if a.type == "object"), None), tool_name=name)
    tool = ToolDefinition(name=name, description=description, inputSchema=schema)
    return ExtractedTool(tool=tool, schema_fidelity="approx", origin_line=line)


def _walk(node: Node) -> list[Node]:
    out = [node]
    for child in node.named_children:
        out.extend(_walk(child))
    return out


def extract_typescript(source: str, *, server_name: str) -> list[ExtractedTool]:
    """Extract every `server.tool(...)` / `server.registerTool(...)` definition in `source`."""
    tree = Parser(_LANGUAGE).parse(source.encode("utf-8"))
    out: list[ExtractedTool] = []
    for node in _walk(tree.root_node):
        if node.type != "call_expression":
            continue
        func = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if func is None or func.type != "member_expression" or arguments is None:
            continue
        prop = func.child_by_field_name("property")
        method = _text(prop) if prop is not None else ""
        if method not in TOOL_METHODS:
            continue
        args = list(arguments.named_children)
        name = _string_value(args[0]) if args else None
        if not name:
            log.info(
                "%s: %s(...) at line %d has no literal name",
                server_name,
                method,
                node.start_point[0] + 1,
            )
            continue
        line = node.start_point[0] + 1
        extracted = (
            _from_register_tool(name, args, line)
            if method == "registerTool"
            else _from_tool(name, args, line)
        )
        if extracted is not None:
            out.append(extracted)
    out.sort(key=lambda e: e.origin_line)
    return out
