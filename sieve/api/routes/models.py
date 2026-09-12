from __future__ import annotations

from fastapi import APIRouter

from sieve.types import ModelInfo

router = APIRouter(tags=["models"])


@router.get("/models", response_model=ModelInfo)
def get_model_info() -> ModelInfo:
    return ModelInfo(
        model_version="fixture@0",
        t_low=0.3,
        t_high=0.85,
        temperature=1.0,
        onnx=False,
        judge_model=None,
        judge_enabled=False,
    )
