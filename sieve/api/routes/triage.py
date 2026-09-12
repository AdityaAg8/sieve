from __future__ import annotations

from fastapi import APIRouter, Depends

from sieve.api.deps import FixtureStore, get_store, stub_classifier
from sieve.serialise import serialise
from sieve.types import LABELS, Label, ToolRecord, TriageResult

router = APIRouter(tags=["triage"])


@router.post("/triage", response_model=TriageResult)
def triage(record: ToolRecord, store: FixtureStore = Depends(get_store)) -> TriageResult:
    fixture = store.template_result(record.tool.name)
    if fixture is not None:
        return fixture

    clf = stub_classifier()
    probs = clf.predict_proba([serialise(record)])[0]
    class_probabilities: dict[Label, float] = dict(
        zip(LABELS, (float(p) for p in probs), strict=True)
    )
    predicted_class = max(class_probabilities, key=lambda k: class_probabilities[k])
    poisoned_p = class_probabilities["poisoned"]
    t_low, t_high = clf.thresholds.t_low, clf.thresholds.t_high
    if poisoned_p >= t_high:
        verdict = "confirmed"
    elif poisoned_p >= t_low:
        verdict = "needs_review"
    else:
        verdict = "dismissed"
    return TriageResult(
        verdict=verdict,
        stage="classifier",
        predicted_class=predicted_class,
        class_probabilities=class_probabilities,
        poisoned_probability=poisoned_p,
        confidence=class_probabilities[predicted_class],
        sub_labels=[],
        owasp=[],
        evidence_spans=[],
        reasoning=None,
        remediation=None,
        scanner_findings=[],
        model_version=clf.model_version,
        judge_model=None,
        latency_ms=1,
    )
