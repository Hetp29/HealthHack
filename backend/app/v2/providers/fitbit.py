"""Fitbit Web API adapter (OAuth 2.0 authorization code + PKCE).

STATUS: written to Fitbit's published Web API and tested against recorded-shape mock
responses. **Never run against Fitbit**: no application is registered.

Before registering one, verify the platform's current state: Google has been moving
Fitbit developers to a Google Health API, and the legacy Web API used here may be
closed to new applications or on a retirement schedule. See BLOCKERS B-006.

Collected: daily steps, resting heart rate, sleep sessions, daily HRV.
Not collected: intraday (minute-level) heart rate. Fitbit gates it behind a separate
approval for anything other than a developer's own account, so it is not requested.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.v2.providers.base import (
    OAuthSpec, ProviderAdapter, ProviderError, SyncResult, WearableObservation, request_json,
)

API = "https://api.fitbit.com"


def _local_day_start(date_text: str, tz: str) -> dt.datetime | None:
    try:
        day = dt.date.fromisoformat(date_text)
        return dt.datetime.combine(day, dt.time(0), tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
    except (ValueError, KeyError):
        return None


def _local_instant(text: str | None, tz: str) -> dt.datetime | None:
    """Fitbit sleep times are local wall-clock with no offset, in the profile's zone."""
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
    except (ValueError, KeyError):
        return None


def normalize_daily_activity(payload: dict, date_text: str, tz: str) -> list[WearableObservation]:
    steps = ((payload or {}).get("summary") or {}).get("steps")
    if steps is None:
        return []
    start = _local_day_start(date_text, tz)
    return [WearableObservation(
        metric="steps_daily", value=float(steps), unit="count", observed_at=start,
        observed_end_at=None if start is None else start + dt.timedelta(days=1),
        source_record_id=f"steps:{date_text}", detail={"local_date": date_text, "timezone": tz})]


def normalize_resting_heart_rate(payload: dict, tz: str) -> list[WearableObservation]:
    out = []
    for day in (payload or {}).get("activities-heart", []):
        resting = (day.get("value") or {}).get("restingHeartRate")
        if resting is None:
            continue
        out.append(WearableObservation(
            metric="resting_heart_rate", value=float(resting), unit="bpm",
            observed_at=_local_day_start(day.get("dateTime", ""), tz),
            source_record_id=f"rhr:{day.get('dateTime')}",
            detail={"local_date": day.get("dateTime"), "timezone": tz}))
    return out


def normalize_sleep(payload: dict, tz: str) -> list[WearableObservation]:
    out = []
    for log in (payload or {}).get("sleep", []):
        minutes = log.get("minutesAsleep")
        if minutes is None or log.get("logId") is None:
            continue
        out.append(WearableObservation(
            metric="sleep_duration", value=float(minutes), unit="min",
            observed_at=_local_instant(log.get("startTime"), tz),
            observed_end_at=_local_instant(log.get("endTime"), tz),
            source_record_id=f"sleep:{log['logId']}",
            detail={"efficiency_percent": log.get("efficiency"),
                    "is_main_sleep": log.get("isMainSleep"), "timezone": tz}))
    return out


def normalize_hrv(payload: dict, tz: str) -> list[WearableObservation]:
    out = []
    for day in (payload or {}).get("hrv", []):
        rmssd = (day.get("value") or {}).get("dailyRmssd")
        if rmssd is None:
            continue
        out.append(WearableObservation(
            metric="hrv_rmssd", value=float(rmssd), unit="ms",
            observed_at=_local_day_start(day.get("dateTime", ""), tz),
            source_record_id=f"hrv:{day.get('dateTime')}",
            detail={"local_date": day.get("dateTime"), "timezone": tz,
                    "measured_during": "sleep"}))
    return out


class FitbitAdapter(ProviderAdapter):
    name = "fitbit"
    kind = "wearable"
    label = "Fitbit"
    metrics = ("steps_daily", "resting_heart_rate", "sleep_duration", "hrv_rmssd")
    not_available = {
        "heart_rate_intraday": "Requires Fitbit's separate intraday-access approval; not requested.",
    }
    spec = OAuthSpec(
        authorize_url="https://www.fitbit.com/oauth2/authorize",
        token_url=f"{API}/oauth2/token",
        revoke_url=f"{API}/oauth2/revoke",
        scopes=("activity", "heartrate", "sleep", "profile"),
        use_pkce=True, client_auth="basic",
    )

    def sync_history(self, client: Any, access_token: str, since: dt.datetime,
                     until: dt.datetime, cursor: str | None) -> SyncResult:
        profile = request_json(client, "GET", f"{API}/1/user/-/profile.json", access_token)
        user = (profile or {}).get("user") or {}
        tz = user.get("timezone")
        if not tz:
            raise ProviderError("profile has no timezone; local times cannot be placed")
        result = SyncResult(external_user_id=user.get("encodedId"))
        zone = ZoneInfo(tz)
        day = since.astimezone(zone).date()
        last = until.astimezone(zone).date()
        while day <= last:
            text = day.isoformat()
            for metric, path, normalize in (
                ("steps_daily", f"/1/user/-/activities/date/{text}.json",
                 lambda p, text=text: normalize_daily_activity(p, text, tz)),
                ("resting_heart_rate", f"/1/user/-/activities/heart/date/{text}/1d.json",
                 lambda p: normalize_resting_heart_rate(p, tz)),
                ("sleep_duration", f"/1.2/user/-/sleep/date/{text}.json",
                 lambda p: normalize_sleep(p, tz)),
                ("hrv_rmssd", f"/1/user/-/hrv/date/{text}.json", lambda p: normalize_hrv(p, tz)),
            ):
                if metric in result.unavailable:
                    continue
                try:
                    result.wearable.extend(normalize(request_json(client, "GET", API + path, access_token)))
                except ProviderError as error:
                    if type(error) is not ProviderError:
                        raise  # auth and rate-limit failures stop the sync
                    # A scope the user declined: skip that metric, keep the rest.
                    result.unavailable[metric] = str(error)
            day += dt.timedelta(days=1)
        result.cursor = until.isoformat()
        return result

    def health_check(self, client: Any, access_token: str) -> dict:
        request_json(client, "GET", f"{API}/1/user/-/profile.json", access_token)
        return {"reachable": True}
