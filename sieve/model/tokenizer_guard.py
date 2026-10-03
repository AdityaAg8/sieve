"""Why the DeBERTa-v3 tokenizer warning is a false positive (training plan B6, finding F13).

transformers 4.57 warns that `microsoft/deberta-v3-base` has "an incorrect regex pattern" and
suggests turning on `fix_mistral_regex`. That fix targets Mistral tokenizers; passing it here would
change every row's ids (400 of 400 measured). The guard compares the fast tokenizer we train
with against the slow sentencepiece reference instead. They agree on every row except where a
character is out of vocabulary: the fast tokenizer emits `[UNK]`, the slow one emits UTF-8
byte-fallback tokens (`<0xE2>` ...). `collapse_unknown` treats those two as the same thing, and
anything else that differs is a real disagreement.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

TOKENIZER_NOTE = (
    "DeBERTa-v3: transformers' 'incorrect regex pattern / fix_mistral_regex' warning is a false "
    "positive here; fast and slow ids agree except [UNK] vs byte fallback (training plan B6, F13)"
)

UNKNOWN = "[UNK]"
_BYTE = re.compile(r"^<0x[0-9A-Fa-f]{2}>$")


def collapse_unknown(tokens: Iterable[str]) -> list[str]:
    """Each run of `[UNK]` or byte-fallback tokens becomes one `[UNK]`."""
    out: list[str] = []
    for token in tokens:
        if token == UNKNOWN or _BYTE.match(token):
            if out and out[-1] == UNKNOWN:
                continue
            out.append(UNKNOWN)
        else:
            out.append(token)
    return out


@dataclass(frozen=True)
class Parity:
    rows: int
    identical: int
    equal_modulo_unknown: int

    @property
    def other_differences(self) -> int:
        return self.rows - self.equal_modulo_unknown


def parity(pairs: Sequence[tuple[Sequence[str], Sequence[str]]]) -> Parity:
    """Agreement of (fast tokens, slow tokens) pairs, raw and modulo the unknown-token gap."""
    identical = sum(list(a) == list(b) for a, b in pairs)
    modulo = sum(collapse_unknown(a) == collapse_unknown(b) for a, b in pairs)
    return Parity(rows=len(pairs), identical=identical, equal_modulo_unknown=modulo)
