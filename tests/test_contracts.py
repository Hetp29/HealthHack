"""Contract v1.0 conformance tests.

These protect the frozen cross-team interface. A failure here means a consumer
(Het's data layer or Neil's frontend) is about to be broken, so these are P0.

Covered:
  * every example payload validates against its generated JSON Schema;
  * the generated schemas are in sync with openapi.yaml (no stale committed output);
  * the structural invariants the master specification freezes (25 points, derived
    read-outs, derived summary metrics, null-bound rule, non-empty limitations);
  * the full endpoint set and the error-code enum.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "contracts"
SCHEMAS = CONTRACTS / "schemas"
EXAMPLES = CONTRACTS / "examples"

CONTRACT_VERSION = "1.0.0"
EXPECTED_MINUTES = list(range(0, 121, 5))
HORIZONS = (30, 60, 90, 120)

# example filename -> schema filename
EXAMPLE_SCHEMA_PAIRS = [
    ("health_response.json", "health_response.schema.json"),
    ("participants_response.json", "participants_response.schema.json"),
    ("history_response.json", "history_response.schema.json"),
    ("predict_request.json", "predict_request.schema.json"),
    ("predict_response.json", "predict_response.schema.json"),
    ("predict_response_generalized_fallback.json", "predict_response.schema.json"),
    ("compare_request.json", "compare_request.schema.json"),
    ("metrics_response.json", "metrics_response.schema.json"),
    ("model_info_response.json", "model_info_response.schema.json"),
    ("error_response.json", "error_response.schema.json"),
]

FORECAST_EXAMPLES = [
    "predict_response.json",
    "predict_response_generalized_fallback.json",
]


def load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def openapi() -> dict:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())


# --------------------------------------------------------------------------- #
# Schema / example validation
# --------------------------------------------------------------------------- #


def test_every_generated_schema_is_itself_a_valid_json_schema():
    schema_files = sorted(SCHEMAS.glob("*.schema.json"))
    assert schema_files, "no generated schemas found; run contracts/build_schemas.py"
    for path in schema_files:
        Draft202012Validator.check_schema(load_json(path))


@pytest.mark.parametrize(("example_name", "schema_name"), EXAMPLE_SCHEMA_PAIRS)
def test_example_validates_against_schema(example_name: str, schema_name: str):
    validator = Draft202012Validator(load_json(SCHEMAS / schema_name))
    instance = load_json(EXAMPLES / example_name)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{list(e.path)}: {e.message}" for e in errors
    )


def test_generated_schemas_are_not_stale(tmp_path):
    """Regenerating from openapi.yaml must reproduce the committed schemas byte-for-byte.

    Guards against someone editing a generated file by hand, or editing openapi.yaml
    without regenerating.
    """
    committed = {p.name: p.read_text() for p in sorted(SCHEMAS.glob("*.schema.json"))}

    result = subprocess.run(
        [sys.executable, str(CONTRACTS / "build_schemas.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    regenerated = {p.name: p.read_text() for p in sorted(SCHEMAS.glob("*.schema.json"))}
    assert regenerated.keys() == committed.keys()
    drifted = [name for name in committed if committed[name] != regenerated[name]]
    assert not drifted, (
        f"generated schemas are stale for {drifted}; "
        "run `python contracts/build_schemas.py` and commit the result"
    )


# --------------------------------------------------------------------------- #
# Endpoint surface
# --------------------------------------------------------------------------- #


def test_contract_version_is_pinned(openapi):
    assert openapi["info"]["version"] == CONTRACT_VERSION


def test_exact_endpoint_set(openapi):
    """The frozen API is exactly these 7 operations -- no more, no fewer."""
    actual = {
        (method.upper(), path)
        for path, operations in openapi["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    expected = {
        ("GET", "/health"),
        ("GET", "/api/participants"),
        ("GET", "/api/participants/{id}/history"),
        ("POST", "/api/predict"),
        ("POST", "/api/compare"),
        ("GET", "/api/model/metrics"),
        ("GET", "/api/model/info"),
    }
    assert actual == expected


def test_predict_declares_the_required_status_codes(openapi):
    responses = openapi["paths"]["/api/predict"]["post"]["responses"]
    for code in ("200", "404", "422", "503"):
        assert code in responses, f"POST /api/predict must document {code}"


def test_error_codes_are_a_closed_enum(openapi):
    codes = set(openapi["components"]["schemas"]["ErrorResponse"]["properties"]["code"]["enum"])
    assert codes == {
        "participant_not_found",
        "invalid_request",
        "naive_datetime",
        "future_context_override",
        "insufficient_premeal_context",
        "model_not_trained",
        "data_layer_unavailable",
        "not_implemented",
        "internal_error",
    }


def test_error_detail_is_an_object(openapi):
    """`detail` must be a JSON object so clients can render it structurally."""
    detail = openapi["components"]["schemas"]["ErrorResponse"]["properties"]["detail"]
    assert detail["type"] == "object"


def test_prediction_status_enum_is_exactly_two_values(openapi):
    enum = openapi["components"]["schemas"]["PredictionStatus"]["enum"]
    assert enum == ["personalized", "generalized_fallback"]


def test_uncertainty_status_enum(openapi):
    enum = openapi["components"]["schemas"]["UncertaintyStatus"]["enum"]
    assert enum == ["not_calibrated", "empirical_holdout"]


def test_points_array_is_pinned_to_25_in_the_schema(openapi):
    points = openapi["components"]["schemas"]["ForecastResponse"]["properties"]["points"]
    assert points["minItems"] == 25
    assert points["maxItems"] == 25


def test_iauc_is_declared_non_negative(openapi):
    """Positive incremental AUC, never signed net excursion."""
    iauc = openapi["components"]["schemas"]["ForecastMetrics"]["properties"][
        "incremental_auc_mg_dl_min"
    ]
    assert iauc["minimum"] == 0


# --------------------------------------------------------------------------- #
# ForecastResponse structural invariants
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FORECAST_EXAMPLES)
def test_exactly_25_points_at_five_minute_spacing(name: str):
    forecast = load_json(EXAMPLES / name)
    minutes = [point["minute"] for point in forecast["points"]]
    assert len(minutes) == 25
    assert minutes == EXPECTED_MINUTES
    assert minutes == sorted(minutes), "minutes must be strictly ascending"


@pytest.mark.parametrize("name", FORECAST_EXAMPLES)
def test_glucose_at_minutes_is_derived_from_points(name: str):
    forecast = load_json(EXAMPLES / name)
    by_minute = {point["minute"]: point["glucose_mg_dl"] for point in forecast["points"]}
    assert set(forecast["glucose_at_minutes"]) == {str(h) for h in HORIZONS}
    for horizon in HORIZONS:
        assert forecast["glucose_at_minutes"][str(horizon)] == by_minute[horizon], (
            f"glucose_at_minutes[{horizon}] disagrees with points[{horizon}]; "
            "it must be a lookup, not an independent value"
        )


@pytest.mark.parametrize("name", FORECAST_EXAMPLES)
def test_summary_metrics_are_derived_from_the_curve(name: str):
    forecast = load_json(EXAMPLES / name)
    points = forecast["points"]
    values = [point["glucose_mg_dl"] for point in points]
    minutes = [point["minute"] for point in points]
    baseline = values[0]
    metrics = forecast["metrics"]

    peak = max(values)
    assert metrics["peak_mg_dl"] == pytest.approx(peak)
    assert metrics["peak_delta_mg_dl"] == pytest.approx(peak - baseline)

    # Earliest-tie peak.
    expected_ttp = next(m for m, v in zip(minutes, values) if v == peak)
    assert metrics["time_to_peak_minutes"] == expected_ttp

    # Five-minute trapezoidal integration of the positive excursion.
    expected_iauc = 0.0
    for i in range(len(minutes) - 1):
        left = max(values[i] - baseline, 0.0)
        right = max(values[i + 1] - baseline, 0.0)
        expected_iauc += 0.5 * (left + right) * (minutes[i + 1] - minutes[i])
    assert metrics["incremental_auc_mg_dl_min"] == pytest.approx(expected_iauc, abs=0.05)
    assert metrics["incremental_auc_mg_dl_min"] >= 0.0


@pytest.mark.parametrize("name", FORECAST_EXAMPLES)
def test_uncounted_uncertainty_means_null_bounds(name: str):
    forecast = load_json(EXAMPLES / name)
    if forecast["uncertainty_status"] != "not_calibrated":
        return
    for point in forecast["points"]:
        assert point["lower_mg_dl"] is None
        assert point["upper_mg_dl"] is None


@pytest.mark.parametrize("name", FORECAST_EXAMPLES)
def test_limitations_always_present(name: str):
    forecast = load_json(EXAMPLES / name)
    assert forecast["limitations"], "limitations must never be empty"
    joined = " ".join(forecast["limitations"]).lower()
    assert "not medical advice" in joined or "not for clinical" in joined


def test_fallback_example_states_its_reason_first():
    """With no dedicated field, limitations[0] carries the fallback reason."""
    forecast = load_json(EXAMPLES / "predict_response_generalized_fallback.json")
    assert forecast["prediction_status"] == "generalized_fallback"
    reason = forecast["limitations"][0].lower()
    assert "generalized" in reason
    assert "minimum" in reason or "below" in reason


def test_metrics_example_is_the_empty_report():
    """The committed metrics example is the pre-evaluation state Neil must render."""
    metrics = load_json(EXAMPLES / "metrics_response.json")
    assert metrics["by_model"] == []
    assert metrics["by_horizon"] == []
    assert metrics["sample_counts"]["test_episodes"] == 0
    assert metrics["limitations"], "an empty report must explain itself"
    assert "no validated metrics" in metrics["limitations"][0].lower()


def test_predict_request_carries_only_prediction_time_information():
    """The request must not be able to express a post-meal observation."""
    request = load_json(EXAMPLES / "predict_request.json")
    assert set(request) == {"participant_id", "meal_timestamp", "meal", "context_override"}
    assert set(request["meal"]) == {
        "carbs_g",
        "protein_g",
        "fat_g",
        "fiber_g",
        "calories_kcal",
    }


def test_all_example_datetimes_are_offset_aware():
    import datetime as dt

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"timestamp", "meal_timestamp", "as_of", "trained_at", "evaluated_at"}:
                    if value is None:
                        continue
                    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    assert parsed.tzinfo is not None, f"{key}={value!r} is offset-naive"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in sorted(EXAMPLES.glob("*.json")):
        walk(load_json(path))
