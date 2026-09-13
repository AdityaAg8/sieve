"""Render a tool definition as untrusted data inside a per-request nonce delimiter (SC-7).

The analysed tool is written by the party under examination, so the block it travels in must
be one the content cannot forge. Two defences, both cheap:

* the delimiters carry a nonce generated for this request only, so a payload that writes a
  plausible closing tag cannot match the one the system prompt told the model to trust; and
* any `</tool_definition` sequence in the content is escaped before wrapping, so there is
  exactly one real closing tag in the block regardless of what the content says.

The field lines come from `sieve.serialise.serialise` — the same text the Stage-1 classifier
scored, so judge and classifier are never reading different renderings of one tool.
"""

from __future__ import annotations

import re
import secrets

from sieve.serialise import serialise
from sieve.types import ToolRecord

OPEN = '<tool_definition nonce="{nonce}">'
CLOSE = '</tool_definition nonce="{nonce}">'

#: A rendered block's nonce, for the replay hash (`sieve.judge.record.request_hash`).
NONCE_RE = re.compile(r'nonce="[0-9a-f]{16}"')

_CLOSE_SEQUENCE = "</tool_definition"
_ESCAPED_CLOSE_SEQUENCE = "<\\/tool_definition"


def new_nonce() -> str:
    """16 hex characters from the CSPRNG. Fresh per request; never reused, never logged."""
    return secrets.token_hex(8)


def escape_delimiters(text: str, nonce: str) -> str:
    """Neutralise anything in `text` that could pass for the block's own delimiters.

    Escaping the closing sequence is what stops content from ending the block early. Redacting
    the live nonce covers the rarer case where the content already contains it — a payload
    echoing a nonce it observed elsewhere, or a tool description that happens to hold the same
    16 hex characters — either of which would otherwise produce a second valid closing tag.
    """
    out = text.replace(_CLOSE_SEQUENCE, _ESCAPED_CLOSE_SEQUENCE)
    if nonce:
        out = out.replace(nonce, "[NONCE]")
    return out


def render_tool_block(record: ToolRecord, *, nonce: str) -> str:
    """The untrusted block exactly as the user message carries it.

    Callers redact the record first (SC-5) and never log the result above DEBUG.
    """
    body = escape_delimiters(serialise(record), nonce)
    return f"{OPEN.format(nonce=nonce)}\n{body}\n{CLOSE.format(nonce=nonce)}"


def strip_nonce(block: str) -> str:
    """The block with its nonce removed, so the same tool hashes the same across requests."""
    return NONCE_RE.sub('nonce=""', block)
