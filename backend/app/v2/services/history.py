"""Assemble the input the frozen engine is given for one forecast.

Nothing in `ml/` is modified. This module decides *what history the engine is shown*;
the engine decides what to do with it, exactly as it did at freeze time.

Three rules are applied here, before the engine's own guards:

**Nothing after the meal.** CGM and meals are selected in SQL with `<= as_of` /
`< as_of`. The engine then truncates again at the same instant. Two independent
filters, and the snapshot stores what survived both.

**Only eligible completed meals count as personal history.** A meal whose response
window has closed is passed to the engine only if its outcome was scored and marked
eligible, and the user consents to personal-history use. A meal whose window is still
open is passed regardless: the engine's own strict rule (window end `<` query time)
already makes it impossible to retrieve, and it is needed for the "time since
previous meal" context.

**The clock is presented the way the model was trained on it.** Each observation is
converted to local wall-clock time with its IANA zone's offset at that observation's
own instant, and the history never spans an offset change. See `project_local_clock`
and `engine_lookback_start`. True UTC stays in storage.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import zlib
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.services import glucose, meal_provenance
from ml.config import HORIZON_WINDOW_MINUTES
from ml.features import ContextOverride, MealComposition
from ml.provenance import SOURCE_LOCAL_UNKNOWN_TZ
from ml.schema import CgmPoint, MealRecord, ParticipantHistory, ParticipantSummary

#: Mirrors `PredictionService._prediction_history`: the fingerprint and retrieval
#: features summarize the participant's own earlier meals, and 8 days matches the
#: prior-context rule the model was evaluated under.
LOOKBACK = dt.timedelta(days=8)
WINDOW = dt.timedelta(minutes=HORIZON_WINDOW_MINUTES)

#: Passed to the engine so it reads time of day straight off the projected clock.
ENGINE_TIME_BASIS = SOURCE_LOCAL_UNKNOWN_TZ


def clock_offset(timezone_name: str, instant: dt.datetime) -> dt.timedelta:
    """The UTC offset in force in an IANA zone at one instant."""
    # `astimezone` first: `ZoneInfo.utcoffset(x)` would read x as LOCAL wall time,
    # which is wrong (and ambiguous) around a daylight-saving change.
    return instant.astimezone(ZoneInfo(timezone_name)).utcoffset() or dt.timedelta(0)


def project_local_clock(instant: dt.datetime, timezone_name: str) -> dt.datetime:
    """Re-express a UTC instant as local wall-clock time carried under a `+00:00` label.

    This is the representation fs-1.3 was trained on: CGMacros stores participant-local
    wall-clock time labelled `+00:00`, and two frozen features read the stored clock
    directly (`g_dev_from_night`, and the time-of-day part of each retrieval vector).
    Given genuine UTC they would read the UTC hour instead.

    The conversion is **per observation**: each instant gets the offset its IANA zone
    had at that instant, so an observation from before a daylight-saving change is
    converted with the pre-change offset. Nothing is stored in this form -- the
    database keeps true UTC; this exists only in the input handed to the engine.
    """
    return instant.astimezone(core.UTC) + clock_offset(timezone_name, instant)


def last_offset_transition(timezone_name: str, start: dt.datetime, end: dt.datetime) -> dt.datetime | None:
    """The most recent instant in `(start, end]` at which the zone's UTC offset
    changed, to the second; `None` when the offset is constant over the interval.

    The returned instant is the first one that has the NEW offset.
    """
    target = clock_offset(timezone_name, end)
    step = dt.timedelta(hours=1)
    probe = end
    while probe > start:
        earlier = max(probe - step, start)
        if clock_offset(timezone_name, earlier) != target:
            low, high = earlier, probe          # offset(low) != target == offset(high)
            while high - low > dt.timedelta(seconds=1):
                middle = low + (high - low) / 2
                if clock_offset(timezone_name, middle) == target:
                    high = middle
                else:
                    low = middle
            return high.replace(microsecond=0) if clock_offset(
                timezone_name, high.replace(microsecond=0)) == target else high
        probe = earlier
    return None


def engine_lookback_start(timezone_name: str, as_of: dt.datetime) -> tuple[dt.datetime, dt.datetime | None]:
    """Where the history shown to the engine begins: `(start, transition)`.

    Normally `as_of - 8 days`. When the zone's offset changed inside that window, the
    history starts AT the change instead.

    Why: local wall-clock time is not a timeline across a daylight-saving change. An
    hour repeats (fall back) or is skipped (spring forward), so per-observation local
    times would be out of order or would mis-state every elapsed time that spans the
    change -- and the frozen feature code assumes an ascending series and computes
    slopes, gaps and response windows from timestamp differences. Showing the engine
    only observations since the change keeps both properties exact: every timestamp it
    sees has its own correct local clock reading, and every difference between two of
    them is the true elapsed time. The cost is a shorter history for up to 8 days
    after a change, which the engine handles as it handles any short history.
    """
    start = as_of - LOOKBACK
    transition = last_offset_transition(timezone_name, start, as_of)
    return (transition, transition) if transition is not None else (start, None)


@dataclass
class EngineInputs:
    history: ParticipantHistory
    participant: ParticipantSummary
    meal_timestamp: dt.datetime
    composition: MealComposition
    override: ContextOverride | None
    context: dict
    canonical: dict

    @property
    def blob(self) -> bytes:
        return zlib.compress(_canonical_bytes(self.canonical), level=6)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.canonical)).hexdigest()


def _canonical_bytes(canonical: dict) -> bytes:
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _iso(value: dt.datetime) -> str:
    return value.astimezone(core.UTC).isoformat()


def eligible_meal_ids(conn: Connection, user_id: str) -> set[str]:
    """Meals whose scored outcome, for their CURRENT confirmation, is eligible."""
    conditions = [
        t.meals.c.user_id == user_id,
        t.response_episodes.c.status == "scored",
        t.response_episodes.c.eligible_for_personalization.is_(True),
    ]
    # Belt and braces for episodes scored before hypothetical meals were told apart.
    authentic = meal_provenance.authentic_meals_only(conn, user_id)
    if authentic is not None:
        conditions.append(authentic)
    rows = conn.execute(
        select(t.meals.c.meal_id)
        .select_from(
            t.meals.join(
                t.predictions,
                and_(t.predictions.c.meal_id == t.meals.c.meal_id,
                     t.predictions.c.confirmation_id == t.meals.c.current_confirmation_id),
            ).join(t.response_episodes,
                   t.response_episodes.c.prediction_id == t.predictions.c.prediction_id)
        )
        .where(and_(*conditions))
    )
    return {row[0] for row in rows}


def build_engine_inputs(
    conn: Connection,
    *,
    user: dict,
    participant_id: str,
    meal: dict,
    confirmation: dict,
    baseline_tolerance_minutes: float,
    personalization_consent: bool,
) -> EngineInputs:
    as_of: dt.datetime = meal["occurred_at"]
    user_id = user["user_id"]
    zone = user["timezone"]
    offset = clock_offset(zone, as_of)
    lookback_start, transition = engine_lookback_start(zone, as_of)

    series, device_choice = glucose.select_model_series(
        conn, user_id, as_of, as_of - lookback_start, baseline_tolerance_minutes
    )

    m, c = t.meals, t.meal_confirmations
    prior = and_(
        m.c.user_id == user_id, m.c.status == "confirmed",
        m.c.meal_id != meal["meal_id"],
        m.c.occurred_at < as_of, m.c.occurred_at >= as_of - LOOKBACK,
        m.c.consumption_status != "not_eaten",
    )
    # In a demonstration account only meals the recorded participant really ate are
    # context: a hypothetical meal is neither a prior meal nor a "time since last
    # meal" for any forecast (DECISIONS D-019).
    authentic = meal_provenance.authentic_meals_only(conn, user_id)
    if authentic is not None:
        prior = and_(prior, authentic)
    prior_rows = conn.execute(
        select(m.c.meal_id, m.c.occurred_at, m.c.consumption_status,
               c.c.carbs_g, c.c.protein_g, c.c.fat_g, c.c.fiber_g, c.c.calories_kcal)
        .select_from(m.join(c, c.c.confirmation_id == m.c.current_confirmation_id))
        .where(prior)
        .order_by(m.c.occurred_at, m.c.meal_id)
    ).mappings().all()

    eligible = eligible_meal_ids(conn, user_id) if personalization_consent else set()
    included, excluded = [], []
    for row in prior_rows:
        window_open = row["occurred_at"] + WINDOW >= as_of
        if row["occurred_at"] < lookback_start:
            # From before an offset change: not shown to the engine (see
            # `engine_lookback_start`). Its timing still reaches it, below.
            excluded.append(row["meal_id"])
        elif window_open or (row["meal_id"] in eligible and row["consumption_status"] == "as_logged"):
            included.append(row)
        else:
            excluded.append(row["meal_id"])

    override = None
    most_recent = prior_rows[-1] if prior_rows else None
    if most_recent is not None and (not included or included[-1]["meal_id"] != most_recent["meal_id"]):
        # The previous meal is real but is not admissible as personal history. Its
        # timing is still legitimate context, supplied through the engine's own
        # `context_override` rather than by showing it the meal.
        gap = (as_of - most_recent["occurred_at"]).total_seconds() / 60.0
        override = ContextOverride(minutes_since_prior_meal=min(gap, 1440.0))

    cgm = [
        CgmPoint(timestamp=project_local_clock(r["observed_at"], zone),
                 glucose_mg_dl=float(r["glucose_mg_dl"]))
        for r in series
    ]
    meals = [
        MealRecord(
            meal_id=r["meal_id"], participant_id=participant_id,
            timestamp=project_local_clock(r["occurred_at"], zone),
            carbs_g=r["carbs_g"], protein_g=r["protein_g"], fat_g=r["fat_g"],
            fiber_g=r["fiber_g"], calories_kcal=r["calories_kcal"],
        )
        for r in included
    ]
    composition = MealComposition(
        carbs_g=float(confirmation["carbs_g"]), protein_g=float(confirmation["protein_g"]),
        fat_g=float(confirmation["fat_g"]), fiber_g=float(confirmation["fiber_g"]),
        calories_kcal=float(confirmation["calories_kcal"]),
    )
    projected_meal_time = project_local_clock(as_of, zone)

    # Invariant the frozen feature code relies on: one offset across everything it is
    # shown, hence an ascending series whose differences are true elapsed times.
    offsets = {clock_offset(zone, r["observed_at"]) for r in series}
    offsets |= {clock_offset(zone, r["occurred_at"]) for r in included}
    if offsets - {offset}:
        raise RuntimeError("engine history spans a UTC-offset change; refusing to forecast")

    canonical = {
        "participant_id": participant_id,
        "meal_timestamp": _iso(projected_meal_time),
        "time_basis": ENGINE_TIME_BASIS,
        "timezone": zone,
        "clock_offset_minutes": offset.total_seconds() / 60.0,
        "composition": dataclasses.asdict(composition),
        "override": None if override is None else {
            "minutes_since_prior_meal": override.minutes_since_prior_meal},
        "cgm": [[_iso(p.timestamp), p.glucose_mg_dl] for p in cgm],
        "meals": [[r.meal_id, _iso(r.timestamp), r.carbs_g, r.protein_g, r.fat_g,
                   r.fiber_g, r.calories_kcal] for r in meals],
        # Deliberately empty. Wearable heart rate and logged medications are stored,
        # but are not shown to the model: see DECISIONS D-008.
        "heart_rate": [],
        "medications": [],
    }
    latest_age = None
    if series:
        latest_age = round((as_of - series[-1]["observed_at"]).total_seconds() / 60.0, 2)
    hour = dt.timedelta(minutes=60)
    context = {
        "as_of": core.rfc3339(as_of),
        "timezone": user["timezone"],
        "clock_offset_minutes": offset.total_seconds() / 60.0,
        "clock_policy": "iana_per_observation",
        "lookback_days": LOOKBACK.days,
        "lookback_start": core.rfc3339(lookback_start),
        "lookback_truncated_at_offset_change": transition is not None,
        "offset_change_at": core.rfc3339(transition),
        "cgm": {
            **device_choice,
            "readings_in_lookback": len(series),
            "readings_last_60_min": sum(1 for r in series if r["observed_at"] >= as_of - hour),
            "latest_age_minutes": latest_age,
            "baseline_tolerance_minutes": baseline_tolerance_minutes,
        },
        "prior_meals": {
            "included_meal_ids": [r["meal_id"] for r in included],
            "excluded_meal_ids": excluded,
            "prior_meal_timing_from_override": override is not None,
        },
        "personalization_consent": personalization_consent,
        "heart_rate_source": "not_supplied",
        "medication_source": "not_supplied",
    }
    return EngineInputs(
        history=ParticipantHistory(participant_id=participant_id, data_source="unknown",
                                   cgm=cgm, meals=meals),
        participant=ParticipantSummary(participant_id=participant_id, display_name="",
                                       data_source="unknown", days_available=0.0),
        meal_timestamp=projected_meal_time,
        composition=composition,
        override=override,
        context=context,
        canonical=canonical,
    )


def inputs_from_snapshot(blob: bytes) -> EngineInputs:
    """Rebuild the exact engine input from a stored snapshot, for reproduction."""
    canonical = json.loads(zlib.decompress(blob).decode("utf-8"))
    parse = dt.datetime.fromisoformat
    participant_id = canonical["participant_id"]
    override = canonical["override"]
    return EngineInputs(
        history=ParticipantHistory(
            participant_id=participant_id, data_source="unknown",
            cgm=[CgmPoint(timestamp=parse(ts), glucose_mg_dl=value) for ts, value in canonical["cgm"]],
            meals=[MealRecord(meal_id=row[0], participant_id=participant_id, timestamp=parse(row[1]),
                              carbs_g=row[2], protein_g=row[3], fat_g=row[4], fiber_g=row[5],
                              calories_kcal=row[6]) for row in canonical["meals"]],
        ),
        participant=ParticipantSummary(participant_id=participant_id, display_name="",
                                       data_source="unknown", days_available=0.0),
        meal_timestamp=parse(canonical["meal_timestamp"]),
        composition=MealComposition(**canonical["composition"]),
        override=None if override is None else ContextOverride(**override),
        context={},
        canonical=canonical,
    )
