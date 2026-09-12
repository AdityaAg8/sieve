"""Corpus collection: registry discovery, static extraction, sandboxed capture.

`ExtractedTool` lives here because both static extractors and the sandbox capture produce
it, and none of them should import another extractor to get at the type.
"""

from __future__ import annotations

from pydantic import BaseModel

from sieve.types import SchemaFidelity, ToolDefinition


class ExtractedTool(BaseModel):
    """One tool definition recovered from source, with how faithful its schema is."""

    tool: ToolDefinition
    schema_fidelity: SchemaFidelity
    origin_line: int


__all__ = ["ExtractedTool"]
