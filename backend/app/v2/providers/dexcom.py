"""Dexcom API adapter (OAuth 2.0, v3 data endpoints).

STATUS: written to Dexcom's published developer API and tested against recorded-shape
mock responses. **Never run against Dexcom**: no developer application is registered
and no credentials exist. Verify against the Dexcom sandbox before relying on it.

What the real interface permits -- stated so nobody promises more:
  * Data is **not real time**. Dexcom's API serves estimated glucose values with a
    delay (on the order of an hour in the US), so a reading from this adapter cannot
    satisfy the model's 15-minute pre-meal baseline rule for a meal being eaten now.
    It is suitable for history and outcome scoring, not for live forecasting.
  * Production access for other people's accounts requires Dexcom's approval of the
    application. The sandbox serves simulated users only -- sandbox data is NOT a
    real person's glucose and is stored with data_class "demo".
  * Regional base URLs differ (US / EU / Japan); only the US and sandbox hosts are
    configured here.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from backend.app.v2.providers.base import (
    OAuthSpec, ProviderAdapter, SyncResult, parse_instant, request_json,
)
from backend.app.v2.services.glucose import IncomingReading

HOSTS = {"sandbox": "https://sandbox-api.dexcom.com", "production": "https://api.dexcom.com"}


def normalize_egvs(payload: dict) -> list[IncomingReading]:
    """`GET /v3/users/self/egvs` -> readings. `systemTime` is the UTC instant."""
    readings: list[IncomingReading] = []
    for record in (payload or {}).get("records", []):
        reading = IncomingReading(
            value=record.get("value"), unit=record.get("unit"),
            observed_at=parse_instant(record.get("systemTime")),
            device_id=str(record.get("transmitterId") or record.get("displayDevice") or "dexcom"),
            source_record_id=record.get("recordId"),
            source_timestamp=record.get("displayTime") or record.get("systemTime"),
        )
        status = record.get("status")
        if status in ("low", "high") or reading.value is None:
            # Outside the sensor's reporting range: the true value is unknown.
            reading.reject_reason = "sensor_range_marker"
        elif status not in (None, "ok", "inRange", "unknown"):
            reading.quality_flags.append(f"provider_status:{status}")
        readings.append(reading)
    return readings


class DexcomAdapter(ProviderAdapter):
    name = "dexcom_api"
    kind = "cgm"
    label = "Dexcom"
    metrics = ("glucose",)
    not_available = {
        "real_time_glucose": "The Dexcom API delays data; it cannot anchor a live forecast.",
    }
    max_sync_days = 30

    def __init__(self, environment: str = "sandbox") -> None:
        self.environment = environment if environment in HOSTS else "sandbox"
        self.host = HOSTS[self.environment]
        self.spec = OAuthSpec(
            authorize_url=f"{self.host}/v2/oauth2/login",
            token_url=f"{self.host}/v2/oauth2/token",
            scopes=("offline_access",),
        )

    @property
    def data_class(self) -> str:
        return "user_device" if self.environment == "production" else "demo"

    def sync_history(self, client: Any, access_token: str, since: dt.datetime,
                     until: dt.datetime, cursor: str | None) -> SyncResult:
        fmt = "%Y-%m-%dT%H:%M:%S"
        payload = request_json(
            client, "GET", f"{self.host}/v3/users/self/egvs", access_token,
            params={"startDate": since.astimezone(dt.timezone.utc).strftime(fmt),
                    "endDate": until.astimezone(dt.timezone.utc).strftime(fmt)},
        )
        return SyncResult(glucose=normalize_egvs(payload), cursor=until.isoformat(),
                          external_user_id=(payload or {}).get("userId"))

    def health_check(self, client: Any, access_token: str) -> dict:
        payload = request_json(client, "GET", f"{self.host}/v3/users/self/dataRange", access_token)
        return {"reachable": True, "has_egvs": bool((payload or {}).get("egvs"))}
