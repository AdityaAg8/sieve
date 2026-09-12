"""Judge prompts. The output models are contracts and live in sieve/protocols.py."""

from __future__ import annotations

import json
from pathlib import Path

from sieve.protocols import JudgeFailure, JudgeOutput, JudgeQuote

__all__ = ["FEWSHOT", "SYSTEM_PROMPT", "JudgeFailure", "JudgeOutput", "JudgeQuote"]

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_PROMPT: str = (_PROMPTS / "system.md").read_text(encoding="utf-8")
FEWSHOT: list[dict[str, object]] = json.loads(
    (_PROMPTS / "fewshot.json").read_text(encoding="utf-8")
)
