"""Concealment transforms (PRD §9.5).

Concealment is an *attribute* of a sample, orthogonal to its semantic tier: a payload can
be `easy` and hidden, or `hard` and in plain sight. Keeping them separate is what lets the
bench report per-mechanism results and lets ablation 13 ask whether a ten-line deterministic
hidden-content detector beats the encoder on a concealment bucket.

`strip_all` is the inverse used by the tests, and by nothing else: it exists so a transform
can be shown to hide text rather than destroy it.
"""

from __future__ import annotations

import re

from sieve.types import Concealment

ZERO_WIDTH_SPACE = "​"
RLO = "‮"  # right-to-left override
PDF = "‬"  # pop directional formatting
ZERO_WIDTH_CHARS = "​‌‍﻿"
BIDI_CHARS = "‪‫‬‭‮⁦⁧⁨⁩"

_HTML_COMMENT_RE = re.compile(r"<!--\s?(.*?)\s?-->", re.DOTALL)
_INVISIBLE_RE = re.compile(f"[{ZERO_WIDTH_CHARS}{BIDI_CHARS}]")


def html_comment(s: str) -> str:
    """Hide the payload in an HTML comment: invisible when the description is rendered."""
    return f"<!-- {s} -->"


def zero_width(s: str, every: int = 3) -> str:
    """Sprinkle zero-width spaces so the text survives reading but not naive matching."""
    chunks = [s[i : i + every] for i in range(0, len(s), every)]
    return ZERO_WIDTH_SPACE.join(chunks)


def bidi_wrap(s: str) -> str:
    """Wrap the payload in bidirectional overrides so terminals render it misleadingly."""
    return f"{RLO}{s}{PDF}"


def whitespace_bury(s: str, lines: int = 12) -> str:
    """Push the payload below a wall of blank lines, out of a reviewer's first screen."""
    return "\n" * lines + s


def strip_all(s: str) -> str:
    """Undo every transform above. Used by tests to prove text was hidden, not mangled."""
    s = _HTML_COMMENT_RE.sub(r"\1", s)
    s = _INVISIBLE_RE.sub("", s)
    return s.strip("\n")


def apply(s: str, mechanism: Concealment) -> str:
    """Dispatch on the mechanism name recorded on the record."""
    if mechanism == "none":
        return s
    if mechanism == "html_comment":
        return html_comment(s)
    if mechanism == "zero_width":
        return zero_width(s)
    if mechanism == "bidi":
        return bidi_wrap(s)
    return whitespace_bury(s)
