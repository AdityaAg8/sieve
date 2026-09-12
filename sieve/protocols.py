"""Cross-lane contracts: protocols, judge models, and the deterministic fakes every lane uses.

Nothing here imports a lane package, and no lane redefines anything here. A lane that needs a
change reports it in its hand-off; changing this file is a contracts PR (PRD §19, §20.1).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from sieve.serialise import serialise
from sieve.types import LABELS, EvidenceField, Label, SubLabel, ToolRecord


@dataclass(frozen=True)
class Thresholds:
    """Persisted in the artefact's sieve_config.json. Never hardcoded (PRD §11.4)."""

    t_low: float
    t_high: float
    temperature: float
    val_recall_at_t_low: float
    val_precision_at_t_high: float
    routing_rate: float


@runtime_checkable
class Classifier(Protocol):
    model_version: str
    thresholds: Thresholds

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        """Calibrated probabilities, shape [n, 3], columns in `sieve.types.LABELS` order."""
        ...


class JudgeQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: EvidenceField
    quote: str


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["confirmed", "dismissed", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    sub_labels: list[SubLabel]
    evidence_quotes: list[JudgeQuote]  # asked for before `reasoning` (PRD §12.3)
    reasoning: str = Field(max_length=400)
    remediation: str = Field(max_length=200)


class JudgeFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["timeout", "api_error", "parse_error", "refusal", "budget_exhausted"]
    detail: str


@runtime_checkable
class Judge(Protocol):
    model: str | None

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure: ...


FAKE_POISON_WORDS: tuple[str, ...] = (
    "id_rsa",
    ".env",
    "credentials",
    "Ignore previous",
    "BCC",
    "curl",
    "environment variables",
)
FAKE_IMPERATIVE_WORDS: tuple[str, ...] = ("MUST", "Always")


def _distribution(p_poisoned: float, *, imperative: bool) -> dict[Label, float]:
    rest = 1.0 - p_poisoned
    if imperative:
        return {"benign": rest * 0.25, "imperative_benign": rest * 0.75, "poisoned": p_poisoned}
    return {"benign": rest * 0.85, "imperative_benign": rest * 0.15, "poisoned": p_poisoned}


def fake_scores(text: str) -> dict[Label, float]:
    """The fake decision rule, exposed so fixtures and tests can assert against one source."""
    imperative = any(w in text for w in FAKE_IMPERATIVE_WORDS)
    if any(w in text for w in FAKE_POISON_WORDS):
        return _distribution(0.95, imperative=imperative)
    if imperative:
        return _distribution(0.55, imperative=True)
    return _distribution(0.05, imperative=False)


class FakeClassifier:
    """Deterministic three-class stand-in: used by every lane's tests and the Phase 0 stub API."""

    model_version = "fake@0"
    thresholds = Thresholds(0.3, 0.85, 1.0, 1.0, 1.0, 0.2)

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        rows = [[fake_scores(t)[lab] for lab in LABELS] for t in texts]
        return np.asarray(rows, dtype=np.float64)


_SENTENCE = re.compile(r"[^.!?\n]*[.!?\n]|[^.!?\n]+")


def _sentence_containing(text: str, needle: str) -> str:
    for m in _SENTENCE.finditer(text):
        if needle in m.group(0):
            return m.group(0).strip()
    return needle


def _quotes(text: str, needle: str) -> list[JudgeQuote]:
    return [JudgeQuote(field="description", quote=_sentence_containing(text, needle))]


class FakeJudge:
    """Deterministic judge stand-in. Same word lists as FakeClassifier so fixtures line up."""

    model: str | None = None

    def judge(self, record: ToolRecord, p_stage1: float) -> JudgeOutput | JudgeFailure:
        text = serialise(record)
        hit = next((w for w in FAKE_POISON_WORDS if w in text), None)
        if hit is not None:
            return JudgeOutput(
                verdict="confirmed",
                confidence=0.9,
                sub_labels=["exfiltration"],
                evidence_quotes=_quotes(text, hit),
                reasoning="Requests a resource outside the tool's stated purpose.",
                remediation="Remove the out-of-scope instruction from the description.",
            )
        imp = next((w for w in FAKE_IMPERATIVE_WORDS if w in text), None)
        if imp is not None:
            return JudgeOutput(
                verdict="dismissed",
                confidence=0.8,
                sub_labels=[],
                evidence_quotes=_quotes(text, imp),
                reasoning="Directive language describing a legitimate call-ordering dependency.",
                remediation="No change needed.",
            )
        return JudgeOutput(
            verdict="uncertain",
            confidence=0.4,
            sub_labels=[],
            evidence_quotes=[],
            reasoning="No out-of-scope instruction and no directive dependency found.",
            remediation="Have a human read the description.",
        )
