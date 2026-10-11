"""Settings were renamed GLUCOTWIN_* -> GLIMPSE_* (2026-10-11).

The new name is read first; the legacy name is still honoured so existing .env files,
running containers and deployment configuration keep working. When both are set, the
new name wins.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.app.config import load_settings
from backend.app.v2 import verify_artifact
from backend.app.v2.settings import env_value, load_v2_settings

ALL_PREFIXES = ("GLIMPSE_", "GLUCOTWIN_")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for prefix in ALL_PREFIXES:
        for name in ("LOG_LEVEL", "DEMO_PASSWORD", "MODEL_ARTIFACT", "EXPECTED_MODEL_SHA256"):
            monkeypatch.delenv(prefix + name, raising=False)


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_v1_config_reads_new_and_legacy_names(monkeypatch, prefix):
    monkeypatch.setenv(prefix + "LOG_LEVEL", "warning")
    assert load_settings().log_level == "WARNING"


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_v2_settings_read_new_and_legacy_names(monkeypatch, prefix):
    monkeypatch.setenv(prefix + "DEMO_PASSWORD", "from-" + prefix)
    assert load_v2_settings().demo_password == "from-" + prefix


def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("GLUCOTWIN_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("GLIMPSE_LOG_LEVEL", "DEBUG")
    assert load_settings().log_level == "DEBUG"
    monkeypatch.setenv("GLUCOTWIN_DEMO_PASSWORD", "legacy")
    monkeypatch.setenv("GLIMPSE_DEMO_PASSWORD", "new")
    assert env_value("GLIMPSE_DEMO_PASSWORD") == "new"


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_artifact_gate_reads_new_and_legacy_names(monkeypatch, tmp_path: pathlib.Path, prefix):
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"x")
    monkeypatch.setenv(prefix + "MODEL_ARTIFACT", str(artifact))
    monkeypatch.setenv(prefix + "EXPECTED_MODEL_SHA256", "0" * 64)
    assert verify_artifact.main() == 1          # read both values, then refused the mismatch
    import hashlib
    monkeypatch.setenv(prefix + "EXPECTED_MODEL_SHA256", hashlib.sha256(b"x").hexdigest())
    assert verify_artifact.main() == 0
