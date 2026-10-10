"""Participant roster and observed history."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Path, Query

from backend.app import errors
from backend.app.dependencies import get_service
from backend.app.schemas import (
    ErrorResponse,
    HistoryResponse,
    ParticipantsResponse,
)
from backend.app.services.prediction import PredictionService

router = APIRouter(prefix="/api", tags=["participants"])

ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Unknown participant"},
    422: {"model": ErrorResponse, "description": "Invalid query parameters"},
    503: {"model": ErrorResponse, "description": "Data layer unavailable"},
}


@router.get(
    "/participants",
    response_model=ParticipantsResponse,
    summary="List available participants",
    responses={503: ERROR_RESPONSES[503]},
)
def list_participants(
    service: PredictionService = Depends(get_service),
) -> ParticipantsResponse:
    return ParticipantsResponse.model_validate(service.participants_payload())


@router.get(
    "/participants/{id}/history",
    response_model=HistoryResponse,
    summary="Observed history for one participant",
    responses=ERROR_RESPONSES,
)
def get_history(
    id: str = Path(min_length=1, max_length=128, description="Opaque participant id"),
    start: dt.datetime | None = Query(
        default=None, description="Inclusive lower bound, RFC3339 with offset"
    ),
    end: dt.datetime | None = Query(
        default=None, description="Inclusive upper bound, RFC3339 with offset"
    ),
    service: PredictionService = Depends(get_service),
) -> HistoryResponse:
    # Query datetimes are validated here rather than via a Pydantic model so the
    # offset-naive case gets the specific `naive_datetime` code.
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise errors.ApiError(
                422,
                errors.NAIVE_DATETIME,
                f"Query parameter {name!r} must be RFC3339 with an explicit UTC offset.",
                {"parameter": name},
            )

    if start is not None and end is not None and start > end:
        raise errors.invalid_request(
            "Query parameter 'start' must not be later than 'end'.",
            {"start": start.isoformat(), "end": end.isoformat()},
        )

    return HistoryResponse.model_validate(service.history_payload(id, start, end))
