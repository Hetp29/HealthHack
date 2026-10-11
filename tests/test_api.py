"""API conformance and failure-mode tests against the running FastAPI app.

Live responses are validated against the frozen JSON Schemas in `contracts/schemas/`,
not against the backend's own Pydantic models. Checking the service against its own
types would only prove internal consistency; checking it against the contract files is
what proves Neil's frontend will not break.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.app.config import load_settings
from backend.app.main import create_app

SCHEMAS = pathlib.Path("contracts/schemas")

PARTICIPANT = "synthetic_p001"
SPARSE_PARTICIPANT = "synthetic_p006"
NO_HR_PARTICIPANT = "synthetic_p005"

# A meal time inside the fixture's seven-day window with dense CGM coverage.
MEAL_TIMESTAMP = "2026-10-05T12:30:00Z"

VALID_MEAL = {
    "carbs_g": 55,
    "protein_g": 22,
    "fat_g": 16,
    "fiber_g": 7,
    "calories_kcal": 460,
}


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / name).read_text()))


def _assert_matches(name: str, payload: object) -> None:
    errors = sorted(_validator(name).iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(load_settings())) as test_client:
        yield test_client


def _predict(client, **overrides):
    body = {
        "participant_id": PARTICIPANT,
        "meal_timestamp": MEAL_TIMESTAMP,
        "meal": dict(VALID_MEAL),
        "context_override": None,
    }
    body.update(overrides)
    return client.post("/api/predict", json=body)


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #


class TestHealth:
    def test_health_is_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        _assert_matches("health_response.schema.json", response.json())
        assert response.json() == {"status": "ok", "version": "1.0.0"}

    def test_health_does_not_depend_on_model_readiness(self, tmp_path):
        """Liveness must stay green so an operator can diagnose a missing artifact."""
        settings = dataclasses.replace(
            load_settings(),
            model_artifact=tmp_path / "absent.joblib",
            metrics_path=tmp_path / "absent.json",
        )
        with TestClient(create_app(settings)) as isolated:
            assert isolated.get("/health").status_code == 200


class TestOpenApiSurface:
    def test_generated_openapi_declares_the_frozen_paths(self, client):
        spec = client.get("/openapi.json").json()
        for path in (
            "/health",
            "/api/participants",
            "/api/participants/{id}/history",
            "/api/predict",
            "/api/compare",
            "/api/model/metrics",
            "/api/model/info",
        ):
            assert path in spec["paths"], f"{path} missing from the served OpenAPI"

    def test_served_version_matches_the_contract(self, client):
        assert client.get("/openapi.json").json()["info"]["version"] == "1.0.0"

    def test_no_extra_api_paths_are_exposed(self, client):
        spec = client.get("/openapi.json").json()
        api_paths = {path for path in spec["paths"] if path.startswith("/api")}
        assert api_paths == {
            "/api/participants",
            "/api/participants/{id}/history",
            "/api/predict",
            "/api/compare",
            "/api/model/metrics",
            "/api/model/info",
        }


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #


class TestParticipants:
    def test_roster_matches_the_contract(self, client):
        response = client.get("/api/participants")
        assert response.status_code == 200
        _assert_matches("participants_response.schema.json", response.json())

    def test_roster_is_sorted_and_labelled(self, client):
        participants = client.get("/api/participants").json()["participants"]
        ids = [p["participant_id"] for p in participants]
        assert ids == sorted(ids)
        for participant in participants:
            assert participant["data_source"] == "synthetic_dev"
            assert participant["days_available"] > 0

    def test_history_matches_the_contract(self, client):
        response = client.get(f"/api/participants/{PARTICIPANT}/history")
        assert response.status_code == 200
        _assert_matches("history_response.schema.json", response.json())

    def test_history_is_chronologically_sorted(self, client):
        payload = client.get(f"/api/participants/{PARTICIPANT}/history").json()
        for key in ("cgm", "meals", "heart_rate"):
            timestamps = [row["timestamp"] for row in payload[key]]
            assert timestamps == sorted(timestamps), f"{key} is not ascending"

    def test_history_is_strictly_filtered_by_participant(self, client):
        payload = client.get(f"/api/participants/{PARTICIPANT}/history").json()
        assert payload["participant_id"] == PARTICIPANT
        for meal in payload["meals"]:
            assert meal["participant_id"] == PARTICIPANT

    def test_absent_modality_is_an_empty_list_not_null(self, client):
        payload = client.get(f"/api/participants/{NO_HR_PARTICIPANT}/history").json()
        assert payload["heart_rate"] == []

    def test_date_range_filter_narrows_the_window(self, client):
        full = client.get(f"/api/participants/{PARTICIPANT}/history").json()
        narrowed = client.get(
            f"/api/participants/{PARTICIPANT}/history",
            params={"start": "2026-10-03T00:00:00Z", "end": "2026-10-04T00:00:00Z"},
        ).json()
        assert 0 < len(narrowed["cgm"]) < len(full["cgm"])
        for point in narrowed["cgm"]:
            assert "2026-10-03" in point["timestamp"] or point["timestamp"].startswith(
                "2026-10-04T00:00:00"
            )

    def test_unknown_participant_is_404_with_the_envelope(self, client):
        response = client.get("/api/participants/nope_p999/history")
        assert response.status_code == 404
        payload = response.json()
        _assert_matches("error_response.schema.json", payload)
        assert payload["code"] == "participant_not_found"
        assert payload["detail"]["participant_id"] == "nope_p999"

    def test_naive_query_datetime_is_422_naive_datetime(self, client):
        response = client.get(
            f"/api/participants/{PARTICIPANT}/history",
            params={"start": "2026-10-03T00:00:00"},
        )
        assert response.status_code == 422
        payload = response.json()
        _assert_matches("error_response.schema.json", payload)
        assert payload["code"] == "naive_datetime"

    def test_inverted_range_is_422(self, client):
        response = client.get(
            f"/api/participants/{PARTICIPANT}/history",
            params={"start": "2026-10-05T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"

    @pytest.mark.parametrize(
        "participant_id",
        ["../../etc/passwd", "..%2F..%2Fetc", "p001;DROP TABLE cgm", "' OR 1=1 --"],
    )
    def test_hostile_participant_ids_are_rejected_not_resolved(
        self, client, participant_id
    ):
        """Ids are opaque lookups, never filesystem paths or query fragments.

        Two safe outcomes, both asserted: a traversal attempt fails to match any
        route at all (404 `invalid_request`, no handler reached), and an injection
        string is treated as an ordinary unknown id (404 `participant_not_found`).
        Either way the envelope is intact and nothing from the host leaks.
        """
        response = client.get(f"/api/participants/{participant_id}/history")
        assert response.status_code in (404, 422), response.status_code
        payload = response.json()
        assert set(payload) == {"code", "message", "detail"}
        assert payload["code"] in ("participant_not_found", "invalid_request")
        # No participant data and nothing from the host filesystem came back.
        assert "cgm" not in payload
        assert "root:" not in response.text


# --------------------------------------------------------------------------- #
# Predict
# --------------------------------------------------------------------------- #


class TestPredictSuccess:
    def test_response_matches_the_frozen_schema(self, client):
        response = _predict(client)
        assert response.status_code == 200, response.text
        _assert_matches("predict_response.schema.json", response.json())

    def test_exactly_25_points(self, client):
        payload = _predict(client).json()
        minutes = [point["minute"] for point in payload["points"]]
        assert minutes == list(range(0, 121, 5))

    def test_glucose_at_minutes_agrees_with_points(self, client):
        payload = _predict(client).json()
        by_minute = {p["minute"]: p["glucose_mg_dl"] for p in payload["points"]}
        for horizon in (30, 60, 90, 120):
            assert payload["glucose_at_minutes"][str(horizon)] == by_minute[horizon]

    def test_metrics_agree_with_the_returned_curve(self, client):
        payload = _predict(client).json()
        values = [p["glucose_mg_dl"] for p in payload["points"]]
        minutes = [p["minute"] for p in payload["points"]]
        peak = max(values)
        assert payload["metrics"]["peak_mg_dl"] == pytest.approx(peak, abs=0.05)
        assert payload["metrics"]["time_to_peak_minutes"] == next(
            m for m, v in zip(minutes, values) if v == peak
        )
        assert payload["metrics"]["incremental_auc_mg_dl_min"] >= 0

    def test_provenance_is_on_every_forecast(self, client):
        payload = _predict(client).json()
        assert payload["data_source"] == "synthetic_dev"
        assert payload["model_version"] == "v0.1.0"
        assert payload["prediction_status"] in ("personalized", "generalized_fallback")
        assert payload["limitations"]

    def test_uncertainty_bounds_are_null(self, client):
        payload = _predict(client).json()
        assert payload["uncertainty_status"] == "not_calibrated"
        assert all(p["lower_mg_dl"] is None for p in payload["points"])
        assert all(p["upper_mg_dl"] is None for p in payload["points"])

    def test_drivers_are_present_and_well_formed(self, client):
        payload = _predict(client).json()
        assert payload["drivers"]
        for driver in payload["drivers"]:
            assert set(driver) == {"feature", "contribution_mg_dl", "explanation"}
            assert driver["explanation"].strip()

    def test_sparse_participant_returns_the_fallback_with_a_reason(self, client):
        payload = _predict(client, participant_id=SPARSE_PARTICIPANT).json()
        assert payload["prediction_status"] == "generalized_fallback"
        assert "generalized cohort model used" in payload["limitations"][0].lower()

    def test_missing_modality_does_not_break_prediction(self, client):
        response = _predict(client, participant_id=NO_HR_PARTICIPANT)
        assert response.status_code == 200
        payload = response.json()
        assert "hr_mean_30min_bpm" in " ".join(payload["limitations"])

    def test_identical_requests_return_identical_forecasts(self, client):
        assert _predict(client).json() == _predict(client).json()

    def test_context_override_changes_the_baseline(self, client):
        plain = _predict(client).json()
        overridden = _predict(
            client, context_override={"premeal_glucose_mg_dl": 160.0}
        ).json()
        assert overridden["points"][0]["glucose_mg_dl"] == pytest.approx(160.0)
        assert overridden["points"][0]["glucose_mg_dl"] != plain["points"][0]["glucose_mg_dl"]

    def test_override_as_of_at_the_meal_time_is_accepted(self, client):
        response = _predict(
            client,
            context_override={
                "premeal_glucose_mg_dl": 120.0,
                "as_of": MEAL_TIMESTAMP,
            },
        )
        assert response.status_code == 200


class TestPredictValidation:
    @pytest.mark.parametrize(
        "bad_meal",
        [
            {**VALID_MEAL, "carbs_g": -1},
            {**VALID_MEAL, "protein_g": -0.5},
            {**VALID_MEAL, "calories_kcal": -100},
            {**VALID_MEAL, "carbs_g": 99999},
            {**VALID_MEAL, "fiber_g": 10_000},
        ],
    )
    def test_out_of_range_macros_are_422(self, client, bad_meal):
        response = _predict(client, meal=bad_meal)
        assert response.status_code == 422
        _assert_matches("error_response.schema.json", response.json())
        assert response.json()["code"] == "invalid_request"

    @pytest.mark.parametrize("missing", list(VALID_MEAL))
    def test_every_macro_is_required(self, client, missing):
        meal = {k: v for k, v in VALID_MEAL.items() if k != missing}
        assert _predict(client, meal=meal).status_code == 422

    @pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf", "abc", None])
    def test_non_finite_and_non_numeric_macros_are_422(self, client, bad_value):
        response = _predict(client, meal={**VALID_MEAL, "carbs_g": bad_value})
        assert response.status_code == 422, f"{bad_value!r} was accepted"

    def test_naive_meal_timestamp_is_422_naive_datetime(self, client):
        response = _predict(client, meal_timestamp="2026-10-05T12:30:00")
        assert response.status_code == 422
        assert response.json()["code"] == "naive_datetime"

    def test_unparseable_timestamp_is_422(self, client):
        assert _predict(client, meal_timestamp="not-a-date").status_code == 422

    def test_unknown_participant_is_404(self, client):
        response = _predict(client, participant_id="nope_p999")
        assert response.status_code == 404
        assert response.json()["code"] == "participant_not_found"

    def test_unknown_participant_takes_priority_over_other_failures(self, client):
        """A 404 must not be masked by a model or context error."""
        response = _predict(client, participant_id="nope_p999", meal_timestamp="2000-01-01T00:00:00Z")
        assert response.status_code == 404

    def test_empty_participant_id_is_422(self, client):
        assert _predict(client, participant_id="").status_code in (404, 422)

    def test_unknown_request_field_is_rejected(self, client):
        response = _predict(client, surprise_field="value")
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"

    def test_future_context_override_is_422(self, client):
        response = _predict(
            client,
            context_override={
                "premeal_glucose_mg_dl": 300.0,
                "as_of": "2026-10-05T13:30:00Z",
            },
        )
        assert response.status_code == 422
        payload = response.json()
        _assert_matches("error_response.schema.json", payload)
        assert payload["code"] in ("future_context_override", "invalid_request")
        assert "meal_timestamp" in json.dumps(payload["detail"])

    def test_meal_time_with_no_premeal_cgm_is_422_insufficient_context(self, client):
        """Years before the fixture window, so no baseline can be measured."""
        response = _predict(client, meal_timestamp="2020-01-01T12:00:00Z")
        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] == "insufficient_premeal_context"
        assert "baseline_tolerance_minutes" in payload["detail"]

    def test_empty_body_is_422(self, client):
        response = client.post("/api/predict", json={})
        assert response.status_code == 422
        _assert_matches("error_response.schema.json", response.json())

    def test_error_detail_is_always_an_object(self, client):
        """A bare string or array would break the frozen envelope."""
        for response in (
            client.post("/api/predict", json={}),
            _predict(client, participant_id="nope_p999"),
            _predict(client, meal_timestamp="2026-10-05T12:30:00"),
        ):
            assert isinstance(response.json()["detail"], dict)

    def test_errors_do_not_echo_submitted_values(self, client):
        """Request bodies carry physiological data; it must not come back in errors."""
        response = _predict(client, meal={**VALID_MEAL, "carbs_g": -12345})
        assert "-12345" not in response.text

    def test_errors_carry_no_traceback(self, client):
        response = _predict(client, meal_timestamp="not-a-date")
        assert "Traceback" not in response.text
        assert "File \"" not in response.text


class TestModelNotTrained:
    @pytest.fixture(scope="class")
    def untrained_client(self, tmp_path_factory):
        directory = tmp_path_factory.mktemp("untrained")
        settings = dataclasses.replace(
            load_settings(),
            model_artifact=directory / "absent.joblib",
            metrics_path=directory / "absent.json",
        )
        with TestClient(create_app(settings)) as test_client:
            yield test_client

    def test_predict_is_503_model_not_trained(self, untrained_client):
        response = _predict(untrained_client)
        assert response.status_code == 503
        payload = response.json()
        _assert_matches("error_response.schema.json", payload)
        assert payload["code"] == "model_not_trained"
        assert "artifact_path" in payload["detail"]

    def test_no_fabricated_success_payload_is_returned(self, untrained_client):
        assert "points" not in _predict(untrained_client).json()

    def test_model_info_is_200_and_reports_none(self, untrained_client):
        response = untrained_client.get("/api/model/info")
        assert response.status_code == 200
        _assert_matches("model_info_response.schema.json", response.json())
        payload = response.json()
        assert payload["model_version"] == "none"
        assert payload["metrics_available"] is False
        assert payload["trained_at"] is None

    def test_metrics_is_200_with_an_explicitly_empty_report(self, untrained_client):
        response = untrained_client.get("/api/model/metrics")
        assert response.status_code == 200
        _assert_matches("metrics_response.schema.json", response.json())
        payload = response.json()
        assert payload["by_model"] == []
        assert payload["by_horizon"] == []
        assert "no validated" in " ".join(payload["limitations"]).lower()
        # `personalization` is optional and NOT nullable in contract v1.0, so with no
        # comparison to report the key must be absent rather than null.
        assert "personalization" not in payload

    def test_participants_still_work_without_a_model(self, untrained_client):
        assert untrained_client.get("/api/participants").status_code == 200

    def test_unknown_participant_still_404s_before_the_503(self, untrained_client):
        response = _predict(untrained_client, participant_id="nope_p999")
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #


class TestCompare:
    def _body(self, ids=(PARTICIPANT, "synthetic_p002")):
        return {
            "participant_ids": list(ids),
            "meal_timestamp": MEAL_TIMESTAMP,
            "meal": dict(VALID_MEAL),
            "context_override": None,
        }

    def test_two_forecasts_and_a_caveat(self, client):
        response = client.post("/api/compare", json=self._body())
        assert response.status_code == 200, response.text
        _assert_matches("compare_response.schema.json", response.json())
        payload = response.json()
        assert len(payload["forecasts"]) == 2
        assert payload["caveat"].strip()

    def test_the_caveat_disclaims_recommendation(self, client):
        caveat = client.post("/api/compare", json=self._body()).json()["caveat"].lower()
        assert "not a dietary recommendation" in caveat
        assert "not a ranking" in caveat

    def test_the_same_meal_yields_different_curves_per_participant(self, client):
        payload = client.post("/api/compare", json=self._body()).json()
        first, second = payload["forecasts"]
        assert first["participant_id"] != second["participant_id"]
        assert first["glucose_at_minutes"] != second["glucose_at_minutes"]

    def test_requires_exactly_two_participants(self, client):
        assert client.post(
            "/api/compare", json=self._body(ids=(PARTICIPANT,))
        ).status_code == 422
        assert client.post(
            "/api/compare",
            json=self._body(ids=(PARTICIPANT, "synthetic_p002", "synthetic_p003")),
        ).status_code == 422

    def test_unknown_participant_is_404(self, client):
        response = client.post(
            "/api/compare", json=self._body(ids=(PARTICIPANT, "nope_p999"))
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Model metadata
# --------------------------------------------------------------------------- #


class TestModelEndpoints:
    def test_info_matches_the_contract(self, client):
        response = client.get("/api/model/info")
        assert response.status_code == 200
        _assert_matches("model_info_response.schema.json", response.json())

    def test_info_reports_real_provenance(self, client):
        payload = client.get("/api/model/info").json()
        assert payload["model_version"] == "v0.1.0"
        assert payload["data_source"] == "synthetic_dev"
        # The feature contract is versioned and inference refuses a mismatched
        # artifact, so this pins the CURRENT version rather than any version.
        # fs-1.1: time-of-day became optional when the local clock is unknowable.
        # fs-1.2: added glucose-dynamics and historical-response-fingerprint signals
        # that improved both within-person and cold-start validation.
        from ml.config import FEATURE_SET_VERSION

        # fs-1.3: retrieval-augmented personalization promoted from research after its
        # cold-start CI excluded zero.
        assert payload["feature_set_version"] == FEATURE_SET_VERSION == "fs-1.3"
        assert payload["trained_at"] is not None
        assert payload["limitations"]

    def test_metrics_matches_the_contract(self, client):
        response = client.get("/api/model/metrics")
        assert response.status_code == 200
        _assert_matches("metrics_response.schema.json", response.json())

    def test_metrics_are_measured_and_carry_sample_counts(self, client):
        payload = client.get("/api/model/metrics").json()
        assert payload["evaluation_protocol"]["split_unit"] == "meal_episode"
        assert payload["sample_counts"]["test_episodes"] > 0
        names = [block["model_name"] for block in payload["by_model"]]
        assert "persistence" in names and "personalized" in names
        for block in payload["by_model"]:
            for row in block["by_horizon"]:
                assert row["n"] > 0

    def test_populated_metrics_include_the_personalization_comparison(self, client):
        payload = client.get("/api/model/metrics").json()
        assert "personalization" in payload
        comparison = payload["personalization"]
        assert comparison["by_participant"]

    def test_metrics_disclose_their_limitations(self, client):
        payload = client.get("/api/model/metrics").json()
        assert payload["limitations"]
        assert "synthetic" in " ".join(payload["limitations"]).lower()


# --------------------------------------------------------------------------- #
# CORS and transport
# --------------------------------------------------------------------------- #


class TestCors:
    def test_allowed_origin_is_echoed(self, client):
        response = client.get(
            "/api/participants", headers={"Origin": "http://localhost:5173"}
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_disallowed_origin_is_not_echoed(self, client):
        response = client.get(
            "/api/participants", headers={"Origin": "https://evil.example.com"}
        )
        assert response.headers.get("access-control-allow-origin") is None

    def test_preflight_is_handled(self, client):
        response = client.options(
            "/api/predict",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code in (200, 204)
        assert "POST" in response.headers.get("access-control-allow-methods", "")

    def test_credentials_are_not_allowed(self, client):
        """The API has no auth or cookies, so allowing credentials would widen nothing."""
        response = client.get(
            "/api/participants", headers={"Origin": "http://localhost:5173"}
        )
        assert response.headers.get("access-control-allow-credentials") is None


class TestMethodAndRouting:
    def test_wrong_method_uses_the_envelope(self, client):
        response = client.get("/api/predict")
        assert response.status_code == 405
        assert set(response.json()) == {"code", "message", "detail"}

    def test_unknown_path_uses_the_envelope(self, client):
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message", "detail"}
