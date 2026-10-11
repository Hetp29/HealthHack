"""The contract every external provider adapter implements.

An adapter knows one provider's URLs and payload shapes. It does not touch the
database, does not hold tokens and does not decide what "connected" means -- that all
lives in `services/devices.py`, once, for every provider.

Internal interface (not a public route):

    get_connection_status()   services/devices.connection_payload
    sync_history()            ProviderAdapter.sync_history
    normalize_readings()      ProviderAdapter.normalize_* (pure functions, unit-tested)
    get_latest_reading()      services/glucose.status
    health_check()            ProviderAdapter.health_check
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from backend.app.v2.services.glucose import IncomingReading


class ProviderError(Exception):
    """Base for failures talking to a provider."""


class ProviderAuthError(ProviderError):
    """The access token was rejected (HTTP 401)."""


class ProviderRevoked(ProviderError):
    """The grant is gone: refresh failed with invalid_grant, or access was withdrawn."""


class ProviderRateLimited(ProviderError):
    def __init__(self, retry_after_seconds: int | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class OAuthSpec:
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    revoke_url: str | None = None
    revoke_method: str = "POST"
    use_pkce: bool = False
    #: "basic" sends client credentials in an Authorization header; "body" in the form.
    client_auth: str = "body"


@dataclass
class WearableObservation:
    metric: str
    value: float | None
    unit: str
    observed_at: dt.datetime | None
    source_record_id: str
    observed_end_at: dt.datetime | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    glucose: list[IncomingReading] = field(default_factory=list)
    wearable: list[WearableObservation] = field(default_factory=list)
    cursor: str | None = None
    external_user_id: str | None = None
    #: Metrics the provider refused or does not expose for this account.
    unavailable: dict[str, str] = field(default_factory=dict)


class ProviderAdapter:
    name: str = ""
    kind: str = "wearable"
    label: str = ""
    spec: OAuthSpec
    #: What this adapter actually reads. Anything not listed is not collected.
    metrics: tuple[str, ...] = ()
    #: Things people expect that the provider does not give us, and why.
    not_available: dict[str, str] = {}
    max_sync_days: int = 7

    def sync_history(self, client: Any, access_token: str, since: dt.datetime,
                     until: dt.datetime, cursor: str | None) -> SyncResult:
        raise NotImplementedError

    def health_check(self, client: Any, access_token: str) -> dict:
        raise NotImplementedError


def request_json(client: Any, method: str, url: str, access_token: str, **kwargs) -> Any:
    """One authenticated call, with the three failures every provider shares mapped."""
    response = client.request(
        method, url, headers={"Authorization": f"Bearer {access_token}",
                              "Accept": "application/json"}, **kwargs)
    if response.status_code == 401:
        raise ProviderAuthError("access token rejected")
    if response.status_code == 429:
        retry = response.headers.get("Retry-After") or response.headers.get("Fitbit-Rate-Limit-Reset")
        raise ProviderRateLimited(int(retry) if retry and retry.isdigit() else None)
    if response.status_code == 403:
        raise ProviderError("forbidden: scope not granted or access not approved")
    if response.status_code >= 400:
        raise ProviderError(f"provider returned HTTP {response.status_code}")
    return response.json() if response.content else None


def parse_instant(text: str | None) -> dt.datetime | None:
    """An RFC 3339 instant with an explicit offset, or `None`. Naive text is refused."""
    if not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(dt.timezone.utc)
