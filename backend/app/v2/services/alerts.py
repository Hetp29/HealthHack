"""Informational forecast alerts.

The server -- not the frontend -- decides whether a forecast crosses the user's chosen
level, so the rule that fires is the rule that is stored. An alert here is a statement
about a model output: it is not a measurement, not a calibrated probability and not a
substitute for a CGM's own alarms, and its payload says so.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context

KIND_FORECAST_ABOVE_THRESHOLD = "forecast_above_threshold"

#: Channels that genuinely deliver today. Anything else is recorded as unavailable
#: rather than reported as sent.
AVAILABLE_CHANNELS = frozenset({"in_app"})
KNOWN_CHANNELS = ("in_app", "push", "email")

DEFAULT_RULE = {
    "enabled": False, "threshold_mg_dl": 180.0, "threshold_source_value": 180.0,
    "threshold_source_unit": "mg/dL", "channels": ["in_app"],
    "quiet_hours_start": None, "quiet_hours_end": None, "cooldown_minutes": 60,
}

DISCLAIMER = (
    "Informational forecast from a research model. Its uncertainty is not calibrated. "
    "It is not a glucose measurement, not medical advice, and does not replace your "
    "CGM's own alerts."
)


def load_rule(conn: Connection, user_id: str) -> dict:
    row = conn.execute(
        select(t.alert_rules).where(t.alert_rules.c.user_id == user_id)
    ).mappings().first()
    return dict(row) if row else {**DEFAULT_RULE, "user_id": user_id, "updated_at": None}


def in_quiet_hours(rule: dict, moment: dt.datetime, timezone_name: str) -> bool:
    start, end = rule.get("quiet_hours_start"), rule.get("quiet_hours_end")
    if not start or not end or start == end:
        return False
    local = moment.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M")
    if start < end:
        return start <= local < end
    return local >= start or local < end  # window wraps midnight


def evaluate_forecast(conn: Connection, ctx: V2Context, user: dict, prediction: dict,
                      cgm_age_minutes: float | None) -> dict | None:
    """Create at most one alert event for a prediction. Returns its payload, if any."""
    rule = load_rule(conn, user["user_id"])
    if not rule["enabled"]:
        return None
    threshold = float(rule["threshold_mg_dl"])
    points = prediction["forecast"]["points"]
    crossing = next((p for p in points if p["minute"] > 0 and p["glucose_mg_dl"] >= threshold), None)
    if crossing is None:
        return None

    now = ctx.now()
    peak = max(points, key=lambda p: p["glucose_mg_dl"])
    status = "active"
    if prediction["prediction_kind"] == "retrospective":
        status = "suppressed_retrospective"
    elif in_quiet_hours(rule, now, user["timezone"]):
        status = "suppressed_quiet_hours"
    else:
        since = now - dt.timedelta(minutes=int(rule["cooldown_minutes"]))
        recent = conn.execute(
            select(t.alert_events.c.alert_id).where(and_(
                t.alert_events.c.user_id == user["user_id"],
                t.alert_events.c.kind == KIND_FORECAST_ABOVE_THRESHOLD,
                t.alert_events.c.status == "active",
                t.alert_events.c.created_at > since,
            )).limit(1)
        ).first()
        if recent is not None:
            status = "suppressed_cooldown"

    unit = rule["threshold_source_unit"]
    payload = {
        "label": "Forecast may rise above your selected level within the next 2 hours",
        "threshold": {"value": rule["threshold_source_value"], "unit": unit,
                      "mg_dl": threshold},
        "predicted_crossing_minute": crossing["minute"],
        "predicted_peak": {"mg_dl": peak["glucose_mg_dl"], "minute": peak["minute"],
                           "in_user_unit": round(core.mg_dl_to_unit(peak["glucose_mg_dl"], unit), 1)},
        "meal_id": prediction["meal_id"],
        "forecast_generated_at": core.rfc3339(prediction["created_at"]),
        "latest_cgm_age_minutes": cgm_age_minutes,
        "uncertainty_status": "not_calibrated",
        "informational_only": True,
        "disclaimer": DISCLAIMER,
    }
    alert_id = core.new_id("alt")
    try:
        with conn.begin_nested():
            conn.execute(t.alert_events.insert().values(
                alert_id=alert_id, user_id=user["user_id"],
                prediction_id=prediction["prediction_id"], kind=KIND_FORECAST_ABOVE_THRESHOLD,
                status=status, payload=payload, created_at=now,
            ))
    except IntegrityError:
        return None  # already evaluated for this prediction

    if status == "active":
        for channel in rule["channels"]:
            record_delivery(conn, ctx, user["user_id"], "alert", alert_id, channel)
    return {"alert_id": alert_id, "status": status, "kind": KIND_FORECAST_ABOVE_THRESHOLD,
            "payload": payload}


def record_delivery(conn: Connection, ctx: V2Context, user_id: str, source_type: str,
                    source_id: str, channel: str) -> None:
    """Record what actually happened on a channel. Never reports an unsent message as sent."""
    available = channel in AVAILABLE_CHANNELS
    try:
        with conn.begin_nested():
            conn.execute(t.notification_deliveries.insert().values(
                delivery_id=core.new_id("ntf"), user_id=user_id, source_type=source_type,
                source_id=source_id, channel=channel,
                status="delivered" if available else "channel_unavailable",
                detail={} if available else {"reason": f"no {channel} service is configured"},
                created_at=ctx.now(),
            ))
    except IntegrityError:
        pass  # one record per source and channel


def alert_payload(row: dict) -> dict:
    return {
        "alert_id": row["alert_id"], "prediction_id": row["prediction_id"],
        "kind": row["kind"], "status": row["status"], "payload": row["payload"],
        "created_at": core.rfc3339(row["created_at"]),
        "acknowledged_at": core.rfc3339(row["acknowledged_at"]),
    }
