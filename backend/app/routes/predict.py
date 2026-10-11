"""Forecast endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.dependencies import get_service
from backend.app.schemas import (
    CompareRequest,
    CompareResponse,
    ErrorResponse,
    ForecastResponse,
    PredictRequest,
)
from backend.app.services.prediction import PredictionService

router = APIRouter(prefix="/api", tags=["prediction"])

ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Unknown participant"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
    503: {"model": ErrorResponse, "description": "No trained model artifact"},
}


@router.post(
    "/predict",
    response_model=ForecastResponse,
    summary="Forecast the postprandial glucose response to one meal",
    responses=ERROR_RESPONSES,
)
def predict(
    request: PredictRequest,
    service: PredictionService = Depends(get_service),
) -> ForecastResponse:
    result = service.predict(
        participant_id=request.participant_id,
        meal_timestamp=request.meal_timestamp,
        meal=request.meal.model_dump(),
        override=(
            request.context_override.model_dump()
            if request.context_override is not None
            else None
        ),
    )
    # Validating the ML layer's dict through the response model means a contract-shape
    # regression fails here, server-side, instead of reaching the frontend as
    # malformed JSON.
    return ForecastResponse.model_validate(result.to_contract_dict())


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="Compare the same meal across two participants",
    responses=ERROR_RESPONSES,
)
def compare(
    request: CompareRequest,
    service: PredictionService = Depends(get_service),
) -> CompareResponse:
    payload = service.compare(
        participant_ids=request.participant_ids,
        meal_timestamp=request.meal_timestamp,
        meal=request.meal.model_dump(),
        override=(
            request.context_override.model_dump()
            if request.context_override is not None
            else None
        ),
    )
    return CompareResponse.model_validate(payload)
