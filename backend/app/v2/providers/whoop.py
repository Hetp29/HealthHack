"""WHOOP developer API adapter (OAuth 2.0, v2 endpoints).

STATUS: written to WHOOP's published developer API and tested against recorded-shape
mock responses. **Never run against WHOOP**: no application is registered.

Collected: recovery (score, resting heart rate, HRV), sleep sessions, workouts.
Not available from WHOOP at all: a continuous heart-rate stream. The API exposes
per-cycle and per-workout summaries only, so there is no minute-level series to store
and none to offer the model.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from backend.app.v2.providers.base import (
    OAuthSpec, ProviderAdapter, ProviderError, SyncResult, WearableObservation, parse_instant,
    request_json,
)

API = "https://api.prod.whoop.com/developer"
MAX_PAGES = 20


def _scored(record: dict) -> dict | None:
    return record.get("score") if record.get("score_state") == "SCORED" else None


def normalize_recovery(payload: dict) -> list[WearableObservation]:
    out = []
    for record in (payload or {}).get("records", []):
        score = _scored(record)
        moment = parse_instant(record.get("created_at"))
        if score is None or record.get("cycle_id") is None:
            continue
        base = f"recovery:{record['cycle_id']}"
        for metric, key, unit in (("recovery_score", "recovery_score", "percent"),
                                  ("resting_heart_rate", "resting_heart_rate", "bpm"),
                                  ("hrv_rmssd", "hrv_rmssd_milli", "ms")):
            if score.get(key) is not None:
                out.append(WearableObservation(
                    metric=metric, value=float(score[key]), unit=unit, observed_at=moment,
                    source_record_id=f"{base}:{metric}",
                    detail={"cycle_id": record["cycle_id"], "user_calibrating": score.get("user_calibrating")}))
    return out


def normalize_sleep(payload: dict) -> list[WearableObservation]:
    out = []
    for record in (payload or {}).get("records", []):
        score = _scored(record)
        if score is None or record.get("id") is None:
            continue
        stages = score.get("stage_summary") or {}
        in_bed, awake = stages.get("total_in_bed_time_milli"), stages.get("total_awake_time_milli")
        if in_bed is None or awake is None:
            continue
        out.append(WearableObservation(
            metric="sleep_duration", value=(in_bed - awake) / 60000.0, unit="min",
            observed_at=parse_instant(record.get("start")),
            observed_end_at=parse_instant(record.get("end")),
            source_record_id=f"sleep:{record['id']}",
            detail={"nap": record.get("nap"),
                    "efficiency_percent": score.get("sleep_efficiency_percentage")}))
    return out


def normalize_workouts(payload: dict) -> list[WearableObservation]:
    out = []
    for record in (payload or {}).get("records", []):
        start, end = parse_instant(record.get("start")), parse_instant(record.get("end"))
        if record.get("id") is None or start is None or end is None:
            continue
        score = _scored(record) or {}
        out.append(WearableObservation(
            metric="workout", value=(end - start).total_seconds() / 60.0, unit="min",
            observed_at=start, observed_end_at=end, source_record_id=f"workout:{record['id']}",
            detail={"sport": record.get("sport_name"), "strain": score.get("strain"),
                    "average_heart_rate_bpm": score.get("average_heart_rate"),
                    "max_heart_rate_bpm": score.get("max_heart_rate")}))
    return out


class WhoopAdapter(ProviderAdapter):
    name = "whoop"
    kind = "wearable"
    label = "WHOOP"
    metrics = ("recovery_score", "resting_heart_rate", "hrv_rmssd", "sleep_duration", "workout")
    not_available = {
        "heart_rate_continuous": "WHOOP's API does not expose a continuous heart-rate stream.",
        "steps_daily": "Not offered by the WHOOP developer API.",
    }
    spec = OAuthSpec(
        authorize_url="https://api.prod.whoop.com/oauth/oauth2/auth",
        token_url="https://api.prod.whoop.com/oauth/oauth2/token",
        revoke_url=f"{API}/v2/user/access", revoke_method="DELETE",
        scopes=("read:recovery", "read:sleep", "read:workout", "offline"),
    )

    def _collection(self, client: Any, token: str, path: str, since: dt.datetime, until: dt.datetime) -> dict:
        records, next_token = [], None
        for _ in range(MAX_PAGES):
            params = {"start": since.isoformat(), "end": until.isoformat(), "limit": 25}
            if next_token:
                params["nextToken"] = next_token
            page = request_json(client, "GET", API + path, token, params=params) or {}
            records.extend(page.get("records", []))
            next_token = page.get("next_token")
            if not next_token:
                break
        return {"records": records}

    def sync_history(self, client: Any, access_token: str, since: dt.datetime,
                     until: dt.datetime, cursor: str | None) -> SyncResult:
        result = SyncResult(cursor=until.isoformat())
        for metric, path, normalize in (
            ("recovery_score", "/v2/recovery", normalize_recovery),
            ("sleep_duration", "/v2/activity/sleep", normalize_sleep),
            ("workout", "/v2/activity/workout", normalize_workouts),
        ):
            try:
                result.wearable.extend(normalize(self._collection(client, access_token, path, since, until)))
            except ProviderError as error:
                if type(error) is not ProviderError:
                    raise
                result.unavailable[metric] = str(error)
        return result

    def health_check(self, client: Any, access_token: str) -> dict:
        request_json(client, "GET", f"{API}/v2/user/profile/basic", access_token)
        return {"reachable": True}
