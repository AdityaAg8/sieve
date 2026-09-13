from __future__ import annotations

from fastapi import APIRouter, Request

from sieve.api.deps import get_classifier, get_judge
from sieve.config import settings
from sieve.types import ModelInfo

router = APIRouter(tags=["models"])


@router.get("/models", response_model=ModelInfo)
async def get_model_info(request: Request) -> ModelInfo:
    """Thresholds come from the artefact, never from a constant here (PRD §11.4)."""
    if getattr(request.app.state, "stub", False):
        return ModelInfo(
            model_version="fixture@0",
            t_low=0.3,
            t_high=0.85,
            temperature=1.0,
            onnx=False,
            judge_model=None,
            judge_enabled=False,
        )
    classifier = get_classifier()
    thresholds = classifier.thresholds
    judge_enabled = settings.judge_enabled
    return ModelInfo(
        model_version=classifier.model_version,
        t_low=thresholds.t_low,
        t_high=thresholds.t_high,
        temperature=thresholds.temperature,
        onnx=settings.use_onnx,
        judge_model=get_judge().model if judge_enabled else None,
        judge_enabled=judge_enabled,
    )
