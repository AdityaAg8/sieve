"""Record judge responses once, replay them forever (PRD §12.4, M6a).

`bench/results/judge_outputs.jsonl` is committed, so a third party reproduces every
judge-dependent metric with no API key and no spend. That only works if the lookup key is
stable across runs, which is why `request_hash` hashes the rendered block with the nonce
stripped — the nonce is fresh per request by design (SC-7) and would otherwise make every
recorded line a one-time pad.

A miss raises. `make bench` must fail loudly rather than quietly drop a judged row and
publish a number computed over fewer findings than it claims.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sieve.judge.render import new_nonce, render_tool_block, strip_nonce
from sieve.protocols import Judge, JudgeFailure, JudgeOutput
from sieve.types import ToolRecord

__all__ = ["RecordingJudge", "ReplayJudge", "ReplayMiss", "request_hash"]


class ReplayMiss(LookupError):  # noqa: N818 - the contract in the lane brief names this type
    """No recorded response for this record. Re-record with SIEVE_RERECORD=1."""


def request_hash(record: ToolRecord, model: str, p_stage1: float) -> str:
    """sha256 over (nonce-stripped rendered block, model, rounded stage-1 score).

    `p_stage1` is part of the prompt, so it is part of the key; it is rounded to three
    decimals first, because an unrounded float from a retrained classifier would miss every
    recorded line even when the prompt is otherwise identical.

    Note that this renders the record **unredacted** — `AnthropicJudge.judge` redacts before
    it renders, so the hashed text is not byte-identical to what was sent. That is deliberate
    and safe: the hash is a local cache key, never transmitted and never stored beside the
    text, and keying on the pre-redaction record keeps the key stable if the redaction
    patterns in `sieve/redact.py` are ever tightened. Only the 64-hex digest is written to
    the cassette; no tool text goes into it.
    """
    block = strip_nonce(render_tool_block(record, nonce=new_nonce()))
    material = f"{model}\n{p_stage1:.3f}\n{block}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _as_output(payload: dict[str, Any]) -> JudgeOutput | JudgeFailure:
    # The line format carries exactly four keys (PRD §12.4), so the two result shapes are
    # told apart by their own fields rather than by a discriminator column.
    if "verdict" in payload:
        return JudgeOutput.model_validate(payload)
    return JudgeFailure.model_validate(payload)


class RecordingJudge:
    """Wraps any Judge, appending one JSONL line per call to `path`. Used once, to record."""

    def __init__(self, inner: Judge, path: Path) -> None:
        self.inner = inner
        self.path = Path(path)
        self.model = inner.model

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure:
        out = self.inner.judge(record, p_stage1)
        usage = getattr(self.inner, "last_usage", None)
        entry = {
            "request_hash": request_hash(record, self.inner.model or "", p_stage1),
            "model": self.inner.model,
            "usage": None if usage is None else usage.model_dump(mode="json"),
            "output": out.model_dump(mode="json"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return out


class ReplayJudge:
    """Answers from a recorded JSONL by request hash. Used by `make bench` (M6a). No network."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        text = self.path.read_text(encoding="utf-8")
        self._by_hash: dict[str, JudgeOutput | JudgeFailure] = {}
        self.model: str | None = None
        for line in text.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if self.model is None:
                self.model = entry["model"]
            self._by_hash[entry["request_hash"]] = _as_output(entry["output"])

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure:
        key = request_hash(record, self.model or "", p_stage1)
        try:
            return self._by_hash[key]
        except KeyError as exc:
            raise ReplayMiss(
                f"no recorded judge response for record {record.id!r} "
                f"(model={self.model!r}, p_stage1={p_stage1:.3f}, hash={key}) in {self.path}; "
                f"re-record with SIEVE_RERECORD=1"
            ) from exc
