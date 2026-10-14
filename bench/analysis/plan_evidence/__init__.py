"""Regenerate every measured number in the training plan (`make plan-evidence`).

A port of the 2026-10-02 audit scripts. Inputs: the corpus, the pinned catalog, and the pinned
third-party clones in `bench/data/external/` (`python -m bench.collect.external`). Intermediate
files hold third-party text, so they live in the gitignored `bench/data/external/plan_evidence/`.
Cross-source comparisons and every model fitted here use dev rows only; the token-length table
counts every corpus row, as `make corpus-stats` does.
"""

from __future__ import annotations

from pathlib import Path

WORK = Path("bench/data/external/plan_evidence")
CORPUS = Path("bench/data/labelled.jsonl")
