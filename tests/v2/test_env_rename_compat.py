"""Settings may be given as GLIMPSE_* or as the original GLUCOTWIN_*.

The application's own (v2) reader looks for the new name first and falls back to the
old one. The frozen v1 engine reads only GLUCOTWIN_* and is not edited; `env_aliases`
copies a GLIMPSE_* value onto its GLUCOTWIN_* name before the engine reads it. When
both are set, the new name wins, in both readers.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from backend.app.config import load_settings
from backend.app.env_aliases import apply_env_aliases, legacy_aliases
from backend.app.v2 import verify_artifact
from backend.app.v2.settings import env_value, load_v2_settings

ALL_PREFIXES = ("GLIMPSE_", "GLUCOTWIN_")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for prefix in ALL_PREFIXES:
        for name in ("LOG_LEVEL", "DEMO_PASSWORD", "MODEL_ARTIFACT", "EXPECTED_MODEL_SHA256"):
            monkeypatch.delenv(prefix + name, raising=False)


def alias(monkeypatch) -> None:
    """What importing `backend.app` does at start-up, undone again after the test."""
    for name, value in legacy_aliases(os.environ).items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_v1_config_reads_new_and_legacy_names(monkeypatch, prefix):
    monkeypatch.setenv(prefix + "LOG_LEVEL", "warning")
    alias(monkeypatch)
    assert load_settings().log_level == "WARNING"


def test_the_frozen_v1_reader_itself_knows_only_the_original_names(monkeypatch):
    """The alias, not an edit to frozen code, is what makes the new names work."""
    monkeypatch.setenv("GLIMPSE_LOG_LEVEL", "warning")
    assert load_settings().log_level == "INFO"


def test_aliasing_is_idempotent_and_leaves_the_original_names_working():
    environ = {"GLUCOTWIN_DB_URL": "sqlite:///legacy.db", "GLIMPSE_LOG_LEVEL": "debug",
               "GLIMPSE_BLANK": "  ", "UNRELATED": "x"}
    assert apply_env_aliases(environ) == {"GLUCOTWIN_LOG_LEVEL": "debug"}
    assert environ["GLUCOTWIN_DB_URL"] == "sqlite:///legacy.db" and "GLUCOTWIN_BLANK" not in environ
    assert apply_env_aliases(environ) == {}


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_v2_settings_read_new_and_legacy_names(monkeypatch, prefix):
    monkeypatch.setenv(prefix + "DEMO_PASSWORD", "from-" + prefix)
    assert load_v2_settings().demo_password == "from-" + prefix


def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("GLUCOTWIN_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("GLIMPSE_LOG_LEVEL", "DEBUG")
    alias(monkeypatch)
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
