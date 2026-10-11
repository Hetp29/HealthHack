"""Demonstration replay controls.

These routes exist ONLY when `GLIMPSE_DEMO_MODE` is enabled; otherwise this router
is never registered and every path here is a 404. They are authenticated, and act
only on the caller's own account, which must be the demo replay account. No replay
route can read or change any other user's data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from backend.app.v2 import core
from backend.app.v2.demo import replay
from backend.app.v2.deps import V2Context, current_user, get_ctx
from backend.app.v2.schemas import Strict

router = APIRouter(prefix="/demo", tags=["demo (only when GLIMPSE_DEMO_MODE is enabled)"])


class AdvanceRequest(Strict):
    #: "next", a checkpoint id, or an alias: retrieval_activation, final_meal, end.
    to: str | None = Field(default=None, max_length=64)
    #: Instead of `to`: let this many minutes pass, releasing readings, stopping at
    #: the next checkpoint without executing it.
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    #: The `event_index` last seen. If the replay has moved since, nothing happens.
    expected_event_index: int | None = Field(default=None, ge=0)


def demo_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_demo"]:
        raise core.V2Error(403, "not_demo_account", "This account is not a demo replay account.")
    return user


@router.get("/replay", summary="Replay position, checkpoints and data provenance")
def get_replay(user: dict = Depends(demo_user), ctx: V2Context = Depends(get_ctx)) -> dict:
    return replay.state(ctx, user["user_id"])


@router.post("/replay/advance", summary="Advance the replay to the next or a named checkpoint")
def advance_replay(
    request: Request,
    body: AdvanceRequest | None = None,
    user: dict = Depends(demo_user),
    ctx: V2Context = Depends(get_ctx),
) -> dict:
    """Moves forward only. Each checkpoint first releases the historical readings up
    to its instant, then performs its action through the ordinary v2 endpoints:
    `meal_forecast` logs the dataset's meal record and requests a forecast;
    `outcome_reveal` asks for the outcome once the response window and grace period
    have passed. Read the results with the normal endpoints (`/meals`,
    `/predictions`, `/meals/{id}/outcome`, `/alerts`, `/cgm/readings`)."""
    body = body or AdvanceRequest()
    if body.to is not None and body.minutes is not None:
        raise core.invalid("Give either 'to' or 'minutes', not both.", {"fields": ["to", "minutes"]})
    return replay.advance(request.app, ctx, user["user_id"], to=body.to or "next",
                          minutes=body.minutes, expected_event_index=body.expected_event_index)


@router.post("/replay/reset", summary="Return the demo account to its initial state")
def reset_replay(
    request: Request,
    user: dict = Depends(demo_user),
    ctx: V2Context = Depends(get_ctx),
) -> dict:
    """Deletes the demo account's data and re-initializes it. The account is
    re-created, so **the current session token stops working**: sign in again with
    the demo credentials. Affects the demo account only."""
    # Resets the caller's own demo mode only; the other mode's account is untouched.
    state = replay.reset(request.app, ctx, replay.mode_of(ctx, user["user_id"]))
    return {**state, "reset": True, "sign_in_again": True}


@router.get("/devices", summary="Simulated connected-health sources and the glucose source")
def list_demo_devices(user: dict = Depends(demo_user), ctx: V2Context = Depends(get_ctx)) -> dict:
    """Every simulated device profile with its status (`inactive` or `demo_simulated`)
    and what it has made visible so far, plus the glucose source -- which is NOT
    simulated: it is a historical research recording being replayed."""
    return replay.devices(ctx, user["user_id"])


@router.post("/devices/{profile}/activate", summary="Switch a simulated source on")
def activate_demo_device(
    profile: str,
    user: dict = Depends(demo_user),
    ctx: V2Context = Depends(get_ctx),
) -> dict:
    """Marks the profile `demo_simulated` and makes its simulated observations up to
    the current replay position visible through `GET /wearables/observations`. Later
    ones appear as the replay advances. **Idempotent.**

    No provider is contacted, no account is linked and no token is created. Profiles:
    `apple_health`, `apple_watch`, `fitbit`, `whoop`."""
    return replay.activate_device(ctx, user["user_id"], profile)


@router.get("/health-timeline", summary="Meals, alerts, doses and wearable summaries in one ordered list")
def demo_health_timeline(
    hours: int = Query(default=24, ge=1, le=24 * 10, description="How far back from the replay position"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(demo_user),
    ctx: V2Context = Depends(get_ctx),
) -> dict:
    """Each item says where it came from (`data_class`, `simulated`, `source`) and
    whether the forecast model uses that kind of data. Glucose readings and heart-rate
    samples are series: read them from `/cgm/readings` and `/wearables/observations`."""
    return replay.health_timeline(ctx, user["user_id"], hours, limit)
