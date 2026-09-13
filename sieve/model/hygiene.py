"""The ten-line unicode-hygiene baseline of ablation 13. Not a product feature.

Sieve is a triage layer, not a scanner (PRD §4.2), so this writes no detection rule of its own:
it reads the counts `sieve.serialise.unicode_flags` already computes and says whether any of
them fired. It exists to answer one question honestly — if a ten-line detector beats the
encoder on a concealment bucket, the encoder is not earning that bucket and the right home for
the capability is preprocessing, not the model (PRD §9.5, §11.5).
"""

from __future__ import annotations

from sieve.serialise import unicode_flags
from sieve.types import ToolRecord


def detect(record: ToolRecord) -> bool:
    """True when any hidden-content count is non-zero."""
    return any(count > 0 for count in unicode_flags(record).values())


def score(record: ToolRecord) -> float:
    """The same decision as a score, so the ablation runner can treat it like a classifier."""
    return 1.0 if detect(record) else 0.0
