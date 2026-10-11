"""Model provenance and measured evaluation results."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.app.dependencies import get_service
from backend.app.schemas import ErrorResponse, MetricsResponse, ModelInfoResponse
from backend.app.services.prediction import PredictionService

router = APIRouter(prefix="/api/model", tags=["model"])


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Measured held-out evaluation results",
    responses={503: {"model": ErrorResponse, "description": "Metrics unavailable"}},
)
def get_metrics(
    service: PredictionService = Depends(get_service),
) -> JSONResponse:
    # Returns an explicitly empty report when no evaluation has been run. There is no
    # code path here that estimates or defaults a metric value.
    #
    # Validate through the typed model first, so a shape regression fails here rather
    # than in the frontend. Then serialize by hand for one reason: `personalization`
    # is OPTIONAL in contract v1.0 and is NOT nullable, so when no comparison exists
    # the key must be absent. Returning `"personalization": null` would validate
    # against the Pydantic model but violate the frozen JSON Schema.
    payload = MetricsResponse.model_validate(service.metrics())
    data = json.loads(payload.model_dump_json(by_alias=True))
    if data.get("personalization") is None:
        data.pop("personalization", None)
    return JSONResponse(content=data)


@router.get(
    "/info",
    response_model=ModelInfoResponse,
    summary="Loaded model provenance and readiness",
)
def get_info(
    service: PredictionService = Depends(get_service),
) -> ModelInfoResponse:
    # Deliberately 200 even with no artifact loaded, so a client can render a
    # readiness state without going through an error path. `model_version` is the
    # literal string "none" in that case.
    return ModelInfoResponse.model_validate(service.model_info())
