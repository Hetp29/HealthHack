"""Which providers exist, and an honest account of what each one can do today."""

from __future__ import annotations

from backend.app.v2.providers.base import ProviderAdapter
from backend.app.v2.providers.dexcom import DexcomAdapter
from backend.app.v2.providers.fitbit import FitbitAdapter
from backend.app.v2.providers.whoop import WhoopAdapter
from backend.app.v2.settings import OAuthClient, V2Settings


def oauth_adapters(settings: V2Settings) -> dict[str, ProviderAdapter]:
    return {
        "dexcom_api": DexcomAdapter(settings.dexcom_environment),
        "fitbit": FitbitAdapter(),
        "whoop": WhoopAdapter(),
    }


def oauth_client(settings: V2Settings, provider: str) -> OAuthClient:
    return {"dexcom_api": settings.dexcom, "fitbit": settings.fitbit,
            "whoop": settings.whoop}[provider]


def missing_configuration(settings: V2Settings, provider: str) -> list[str]:
    """Names (never values) of the settings still needed before a connect can start."""
    env = {"dexcom_api": "DEXCOM", "fitbit": "FITBIT", "whoop": "WHOOP"}[provider]
    client = oauth_client(settings, provider)
    missing = []
    if not client.client_id:
        missing.append(f"{env}_CLIENT_ID")
    if not client.client_secret:
        missing.append(f"{env}_CLIENT_SECRET")
    if not settings.token_encryption_key:
        missing.append("GLUCOTWIN_TOKEN_ENCRYPTION_KEY")
    if not settings.public_api_base_url:
        missing.append("GLUCOTWIN_PUBLIC_API_BASE_URL")
    return missing


def capability_matrix(settings: V2Settings) -> list[dict]:
    """Per provider: how it integrates, what it reads, what it cannot, and whether it
    has ever been exercised against the real service."""
    rows: list[dict] = [
        {"provider": "dexcom_clarity_csv", "label": "Dexcom Clarity export", "kind": "cgm",
         "integration": "file_import", "configured": True, "metrics": ["glucose"],
         "not_available": {}, "verification": "fixture_tested_not_verified_with_real_export"},
        {"provider": "libreview_csv", "label": "LibreView export", "kind": "cgm",
         "integration": "file_import", "configured": True, "metrics": ["glucose"],
         "not_available": {
             "retrieval_personalization": "LibreView exports the historic series at a "
             "15-minute interval. The frozen model needs 20 readings inside a 120-minute "
             "response before it will use a past meal, so Libre history cannot activate it.",
             "provider_api": "Abbott offers no third-party API for LibreView."},
         "verification": "fixture_tested_not_verified_with_real_export"},
        {"provider": "manual_entry", "label": "Manual entry", "kind": "cgm",
         "integration": "api_push", "configured": True, "metrics": ["glucose"],
         "not_available": {}, "verification": "tested"},
        {"provider": "healthkit_bridge", "label": "Apple Health / Apple Watch", "kind": "wearable",
         "integration": "native_bridge", "configured": False,
         "metrics": ["glucose", "heart_rate", "steps_daily", "sleep_duration", "hrv_sdnn", "workout"],
         "not_available": {"browser_access": "HealthKit cannot be read from a web browser. "
                           "A native iOS app with the HealthKit entitlement must read it and "
                           "upload to POST /cgm/readings and POST /wearables/observations."},
         "verification": "ingest_endpoint_tested_no_native_app_exists"},
    ]
    for name, adapter in oauth_adapters(settings).items():
        missing = missing_configuration(settings, name)
        rows.append({
            "provider": name, "label": adapter.label, "kind": adapter.kind,
            "integration": "oauth_api", "configured": not missing,
            "missing_configuration": missing, "metrics": list(adapter.metrics),
            "scopes": list(adapter.spec.scopes), "not_available": dict(adapter.not_available),
            "verification": "mock_tested_never_run_against_provider",
            **({"environment": adapter.environment} if hasattr(adapter, "environment") else {}),
        })
    return rows
