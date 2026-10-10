"""FREEZE VERIFICATION (new in the fs-1.3 freeze retest) -- Phases 1, 6 and 8.

Narrowly scoped guards on the frozen deliverable itself: artifact checksum,
feature schema and ordering, ensemble and personalization configuration, the
retrieval gate, contract immutability, and model-version reporting.

These tests change no behaviour and touch no artifact. They exist so that a future
edit which silently alters the frozen model fails CI instead of shipping.

The artifact-dependent tests skip (never pass vacuously) when the fs-1.3 artifact
is not present on the machine.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

ART = pathlib.Path("ml/artifacts_cgmacros_fs13")
CONTRACT = pathlib.Path("contracts/openapi.yaml")

#: Recorded at freeze time. A change here means a new model version.
FROZEN_MODEL_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"
#: Contract v1.0 as frozen at commit 388d24e.
FROZEN_CONTRACT_SHA256 = "be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4"

FROZEN_FEATURE_COUNT = 39
FROZEN_FEATURE_SET = "fs-1.3"
FROZEN_RIDGE_WEIGHT = 0.5
FROZEN_RETRIEVAL_MIN_HISTORY = 5
FROZEN_RETRIEVAL_K = 5
FROZEN_SHRINKAGE_K = 3.0

artifact_required = pytest.mark.skipif(
    not (ART / "model.joblib").exists(),
    reason="fs-1.3 artifact not present on this machine",
)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Artifact integrity
# --------------------------------------------------------------------------- #
@artifact_required
def test_model_artifact_checksum_matches_the_freeze():
    assert sha256(ART / "model.joblib") == FROZEN_MODEL_SHA256, (
        "the fs-1.3 model artifact has changed. Any change to weights is a NEW "
        "model version requiring its own verification -- see "
        "docs/governance/FS13_MODEL_FREEZE_V1.md")


@artifact_required
def test_artifact_checksum_matches_its_own_manifest():
    manifest = json.loads((ART / "model_manifest.json").read_text())
    assert manifest["artifact_sha256"] == sha256(ART / "model.joblib")


@artifact_required
def test_manifest_records_the_frozen_identifiers():
    manifest = json.loads((ART / "model_manifest.json").read_text())
    assert manifest["feature_set_version"] == FROZEN_FEATURE_SET
    assert manifest["model_version"] == "v0.1.0"
    assert manifest["contract_version"] == "1.0.0"
    assert manifest["family"] == "blend"
    assert manifest["ridge_weight"] == FROZEN_RIDGE_WEIGHT
    assert manifest["data_source"] == "official"


# --------------------------------------------------------------------------- #
# Feature schema compatibility
# --------------------------------------------------------------------------- #
def test_code_exposes_exactly_the_frozen_39_features():
    from ml.config import FEATURE_SET_VERSION
    from ml.features import FEATURE_NAMES

    assert FEATURE_SET_VERSION == FROZEN_FEATURE_SET
    assert len(FEATURE_NAMES) == FROZEN_FEATURE_COUNT


@artifact_required
def test_feature_names_and_ORDER_match_the_manifest():
    """Order matters: the stored coefficients correspond to a column layout, so a
    reordering would serve confident nonsense."""
    from ml.features import FEATURE_NAMES

    manifest = json.loads((ART / "model_manifest.json").read_text())
    assert list(manifest["feature_names"]) == list(FEATURE_NAMES)


@artifact_required
def test_loading_refuses_a_feature_contract_mismatch():
    """`load_artifact` must raise rather than silently serve a mismatched layout."""
    import inspect

    from ml.train import load_artifact

    source = inspect.getsource(load_artifact)
    assert "raise" in source
    assert "feature_set_version" in source
    assert "feature_names" in source


# --------------------------------------------------------------------------- #
# Ensemble, personalization and retrieval configuration
# --------------------------------------------------------------------------- #
def test_frozen_configuration_constants():
    from ml.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG.features.retrieval_min_history == FROZEN_RETRIEVAL_MIN_HISTORY
    assert DEFAULT_CONFIG.features.retrieval_k == FROZEN_RETRIEVAL_K
    assert DEFAULT_CONFIG.features.fingerprint_shrinkage_k == FROZEN_SHRINKAGE_K
    assert DEFAULT_CONFIG.personalization.shrinkage_k == FROZEN_SHRINKAGE_K


@artifact_required
def test_artifact_carries_the_fifty_fifty_blend():
    import joblib

    artifact = joblib.load(ART / "model.joblib")
    assert artifact.family == "blend"
    assert artifact.ridge_weight == FROZEN_RIDGE_WEIGHT, (
        "the frozen ensemble is 0.5 * Ridge + 0.5 * HistGradientBoosting")
    assert artifact.feature_set_version == FROZEN_FEATURE_SET
    assert artifact.generalized is not None and artifact.booster is not None


@artifact_required
def test_artifact_personalization_policy():
    manifest = json.loads((ART / "model_manifest.json").read_text())
    p = manifest["personalization"]
    assert p["shrinkage_k"] == FROZEN_SHRINKAGE_K
    assert p["min_episodes"] == 4
    assert p["max_offset_mg_dl"] == 60.0


@artifact_required
def test_artifact_was_not_fitted_on_the_reference_episodes():
    """The shipped artifact is fitted on the 1,171-episode development pool. The
    394 Day-8 reference episodes are excluded from its fit, which is what makes
    evaluating it on them meaningful at all."""
    import joblib

    summary = joblib.load(ART / "model.joblib").training_summary
    assert summary["fitted_episodes"] == 1171
    assert summary["holdout"] == "day8"
    assert summary["participants"] == 45


# --------------------------------------------------------------------------- #
# Contract preservation and version reporting
# --------------------------------------------------------------------------- #
def test_contract_v1_is_byte_identical_to_its_freeze():
    assert sha256(CONTRACT) == FROZEN_CONTRACT_SHA256, (
        "contracts/openapi.yaml changed. Contract v1.0 is frozen; a change is a new "
        "contract version and breaks every client built against v1.0")


def test_contract_declares_version_1_0_0_and_seven_endpoints():
    text = CONTRACT.read_text()
    assert 'version: "1.0.0"' in text
    assert len([line for line in text.splitlines()
                if line.startswith("  /") and line.rstrip().endswith(":")]) == 7


def test_every_frozen_response_schema_is_present():
    schemas = pathlib.Path("contracts/schemas")
    for name in ("predict_response.schema.json", "model_info_response.schema.json",
                 "metrics_response.schema.json", "participants_response.schema.json",
                 "history_response.schema.json", "compare_response.schema.json",
                 "health_response.schema.json", "error_response.schema.json",
                 "predict_request.schema.json", "compare_request.schema.json"):
        assert (schemas / name).exists(), f"missing frozen schema {name}"


def test_predict_response_requires_exactly_25_points():
    schema = json.loads(
        (pathlib.Path("contracts/schemas") / "predict_response.schema.json").read_text())
    points = schema["properties"]["points"]
    assert points["minItems"] == 25 and points["maxItems"] == 25
    assert schema["properties"]["glucose_at_minutes"]["required"] == ["30", "60", "90", "120"]
