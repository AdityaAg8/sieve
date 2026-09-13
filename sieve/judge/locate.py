"""Turn the judge's quotes into character offsets, locally (PRD §12.2).

The model is asked for quotes, not offsets, for two reasons: offsets are the thing language
models are worst at, and a fabricated offset is invisible while a fabricated quote is not.
Locating happens here, against the same text the model saw, and anything that will not
locate is kept with `start = end = None` and counted — the count is a quality signal about
the judge, so it is returned rather than swallowed.

Three strategies per quote, in order, each tried across every candidate string in the named
field before the next is tried: exact substring, case- and whitespace-normalised, then a
`difflib` best window with ratio >= 0.9. A quote is only ever searched in the field the
judge named; a judge that attributes a quote to the wrong field is reporting something worth
seeing, not something to paper over.
"""

from __future__ import annotations

from collections.abc import Iterator
from difflib import SequenceMatcher
from typing import Any

from sieve.protocols import JudgeQuote
from sieve.types import EvidenceField, EvidenceSpan, ToolRecord

__all__ = ["FUZZY_THRESHOLD", "locate"]

FUZZY_THRESHOLD = 0.9

_SCHEMA_FIELDS: frozenset[str] = frozenset({"inputSchema", "outputSchema", "annotations"})
_LENGTH_FACTORS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3)


def _escape(token: str) -> str:
    """RFC 6901 pointer escaping: `~` then `/`, in that order."""
    return token.replace("~", "~0").replace("/", "~1")


def _leaves(node: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Every string *value* in the structure, with its JSON pointer. Keys are not values."""
    if isinstance(node, str):
        yield pointer, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{pointer}/{_escape(str(key))}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, f"{pointer}/{index}")


def _candidates(record: ToolRecord, field: EvidenceField) -> list[tuple[str | None, str]]:
    tool = record.tool
    if field in _SCHEMA_FIELDS:
        value = getattr(tool, field, None)
        if not isinstance(value, dict):
            return []
        return [(pointer, text) for pointer, text in _leaves(value) if text]
    text = getattr(tool, field, None)
    return [(None, text)] if isinstance(text, str) and text else []


def _fold(text: str) -> tuple[str, list[int]]:
    """Lowercase with whitespace runs collapsed, plus a map back to original indices."""
    folded: list[str] = []
    index: list[int] = []
    in_space = False
    for position, char in enumerate(text):
        if char.isspace():
            if in_space:
                continue
            folded.append(" ")
            index.append(position)
            in_space = True
        else:
            folded.append(char.lower())
            index.append(position)
            in_space = False
    return "".join(folded), index


def _span_from_folded(index: list[int], start: int, length: int, text_len: int) -> tuple[int, int]:
    first = index[start]
    last = index[start + length - 1]
    return first, min(last + 1, text_len)


def _fuzzy(folded_text: str, folded_quote: str) -> tuple[float, int, int] | None:
    """Best window of `folded_text` similar to `folded_quote`, anchored to bound the search."""
    n = len(folded_quote)
    if n == 0 or not folded_text:
        return None

    anchor = SequenceMatcher(None, folded_text, folded_quote, autojunk=False).find_longest_match(
        0, len(folded_text), 0, n
    )
    if anchor.size == 0:
        return None
    centre = anchor.a - anchor.b
    low = max(0, centre - n // 2)
    high = min(len(folded_text), centre + n + n // 2)

    matcher = SequenceMatcher(None, "", folded_quote, autojunk=False)
    best: tuple[float, int, int] | None = None
    for length in sorted({max(1, int(n * factor)) for factor in _LENGTH_FACTORS}):
        for start in range(low, max(low + 1, high - length + 1)):
            matcher.set_seq1(folded_text[start : start + length])
            if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
                continue
            if matcher.quick_ratio() < FUZZY_THRESHOLD:
                continue
            ratio = matcher.ratio()
            if ratio >= FUZZY_THRESHOLD and (best is None or ratio > best[0]):
                best = (ratio, start, start + length)
    return best


def _locate_one(
    candidates: list[tuple[str | None, str]], field: EvidenceField, quote: JudgeQuote
) -> EvidenceSpan:
    needle = quote.quote

    for pointer, text in candidates:
        position = text.find(needle)
        if position >= 0:
            return EvidenceSpan(
                field=field,
                path=pointer,
                quote=needle,
                start=position,
                end=position + len(needle),
            )

    folded_quote, _ = _fold(needle.strip())
    folded_quote = folded_quote.strip()
    folded: list[tuple[str | None, str, str, list[int]]] = []
    for pointer, text in candidates:
        folded_text, index = _fold(text)
        folded.append((pointer, text, folded_text, index))

    if folded_quote:
        for pointer, text, folded_text, index in folded:
            position = folded_text.find(folded_quote)
            if position >= 0:
                start, end = _span_from_folded(index, position, len(folded_quote), len(text))
                return EvidenceSpan(field=field, path=pointer, quote=needle, start=start, end=end)

        best: tuple[float, str | None, int, int] | None = None
        for pointer, text, folded_text, index in folded:
            hit = _fuzzy(folded_text, folded_quote)
            if hit is None:
                continue
            ratio, window_start, window_end = hit
            if best is not None and ratio <= best[0]:
                continue
            start, end = _span_from_folded(
                index, window_start, window_end - window_start, len(text)
            )
            best = (ratio, pointer, start, end)
        if best is not None:
            _, pointer, start, end = best
            return EvidenceSpan(field=field, path=pointer, quote=needle, start=start, end=end)

    return EvidenceSpan(field=field, path=None, quote=needle, start=None, end=None)


def locate(record: ToolRecord, quotes: list[JudgeQuote]) -> tuple[list[EvidenceSpan], int]:
    """Spans for every quote, in order, plus the number that could not be located."""
    spans: list[EvidenceSpan] = []
    unlocated = 0
    for quote in quotes:
        candidates = _candidates(record, quote.field)
        span = _locate_one(candidates, quote.field, quote)
        if span.start is None:
            unlocated += 1
        spans.append(span)
    return spans, unlocated
