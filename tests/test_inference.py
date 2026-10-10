"""Forecast output must satisfy every frozen contract invariant."""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

from ml.config import DEFAULT_CONFIG, FORECAST_MINUTES, HORIZONS
from ml.devfixture import SyntheticDevRepository
from ml.features import ContextOverride, InsufficientPremealContext, MealComposition
from ml.inference import forecast
from ml.metrics import incremental_auc
from ml.schema import CgmPoint, ParticipantHistory
from ml.train import train

SCHEMA_PATH = pathlib.Path("contracts/schemas/predict_response.schema.json")


@pytest.fixture(scope="module")
def artifact():
    return train(SyntheticDevRepository(), DEFAULT_CONFIG)


@pytest.fixture(scope="module")
def repository():
    return SyntheticDevRepository()


@pytest.fixture(scope="module")
def schema_validator():
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def _forecast(artifact, repository, participant_id: str, meal_index: int = -1, **kwargs):
    history = repository.get_history(participant_id)
    summary = repository.get_participant(participant_id)
    meal = history.meals[meal_index]
    return forecast(
        artifact,
        history,
        meal.timestamp,
        MealComposition.from_meal_record(meal),
        participant=summary,
        **kwargs,
    )


ALL_PARTICIPANTS = [
    "synthetic_p001",
    "synthetic_p002",
    "synthetic_p003",
    "synthetic_p004",
    "synthetic_p005",
    "synthetic_p006",
]


class TestContractConformance:
    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_output_validates_against_the_frozen_schema(
        self, artifact, repository, schema_validator, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        errors = sorted(schema_validator.iter_errors(payload), key=lambda e: list(e.path))
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)

    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_exactly_25_points_in_ascending_minute_order(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        minutes = [point["minute"] for point in payload["points"]]
        assert len(minutes) == 25
        assert minutes == list(FORECAST_MINUTES)

    def test_minute_zero_is_the_measured_baseline(self, artifact, repository):
        result = _forecast(artifact, repository, "synthetic_p001")
        assert result.points[0].glucose_mg_dl == result.baseline_glucose_mg_dl

    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_glucose_at_minutes_is_a_lookup_into_points(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        by_minute = {p["minute"]: p["glucose_mg_dl"] for p in payload["points"]}
        for horizon in HORIZONS:
            assert payload["glucose_at_minutes"][str(horizon)] == by_minute[horizon]

    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_metrics_are_derived_from_the_returned_curve(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        minutes = [p["minute"] for p in payload["points"]]
        values = [p["glucose_mg_dl"] for p in payload["points"]]
        baseline = values[0]
        metrics = payload["metrics"]

        peak = max(values)
        assert metrics["peak_mg_dl"] == pytest.approx(peak, abs=0.05)
        assert metrics["peak_delta_mg_dl"] == pytest.approx(peak - baseline, abs=0.05)
        expected_ttp = next(m for m, v in zip(minutes, values) if v == peak)
        assert metrics["time_to_peak_minutes"] == expected_ttp
        assert metrics["incremental_auc_mg_dl_min"] == pytest.approx(
            incremental_auc(minutes, values, baseline), abs=1.0
        )

    def test_iauc_is_never_negative(self, artifact, repository):
        for participant_id in ALL_PARTICIPANTS:
            for index in (0, 1, -1):
                payload = _forecast(
                    artifact, repository, participant_id, meal_index=index
                ).to_contract_dict()
                assert payload["metrics"]["incremental_auc_mg_dl_min"] >= 0.0

    def test_time_to_peak_lands_on_a_knot(self, artifact, repository):
        """A documented consequence of piecewise-linear interpolation."""
        payload = _forecast(artifact, repository, "synthetic_p002").to_contract_dict()
        assert payload["metrics"]["time_to_peak_minutes"] in (0, *HORIZONS)

    def test_meal_timestamp_is_echoed_as_utc_rfc3339(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p001").to_contract_dict()
        assert payload["meal_timestamp"].endswith("Z")
        parsed = dt.datetime.fromisoformat(
            payload["meal_timestamp"].replace("Z", "+00:00")
        )
        assert parsed.tzinfo is not None


class TestUncertainty:
    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_bounds_are_null_while_uncalibrated(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        assert payload["uncertainty_status"] == "not_calibrated"
        for point in payload["points"]:
            assert point["lower_mg_dl"] is None
            assert point["upper_mg_dl"] is None

    def test_the_absence_of_intervals_is_disclosed(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p001").to_contract_dict()
        joined = " ".join(payload["limitations"]).lower()
        assert "not calibrated" in joined


class TestPredictionStatus:
    def test_a_well_logged_participant_is_personalized(self, artifact, repository):
        result = _forecast(artifact, repository, "synthetic_p002")
        assert result.prediction_status == "personalized"

    def test_a_sparse_logger_falls_back(self, artifact, repository):
        result = _forecast(artifact, repository, "synthetic_p006")
        assert result.prediction_status == "generalized_fallback"

    def test_the_fallback_reason_is_the_first_limitation(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p006").to_contract_dict()
        reason = payload["limitations"][0].lower()
        assert "generalized cohort model used" in reason
        assert "minimum" in reason

    def test_an_unknown_participant_history_falls_back_rather_than_raising(
        self, artifact, repository
    ):
        """A brand-new user is a legitimate fallback, not an error."""
        source = repository.get_history("synthetic_p001")
        unseen = ParticipantHistory(
            participant_id="brand_new_user",
            data_source="synthetic_dev",
            cgm=source.cgm,
            meals=source.meals,
            heart_rate=source.heart_rate,
        )
        meal = unseen.meals[-1]
        result = forecast(
            artifact, unseen, meal.timestamp, MealComposition.from_meal_record(meal)
        )
        assert result.prediction_status == "generalized_fallback"
        assert "no meal episodes in the training data" in result.limitations[0]

    def test_personalization_actually_shifts_the_prediction(self, artifact, repository):
        history = repository.get_history("synthetic_p002")
        summary = repository.get_participant("synthetic_p002")
        meal = history.meals[-1]
        composition = MealComposition.from_meal_record(meal)

        personalized = forecast(
            artifact, history, meal.timestamp, composition, participant=summary
        )
        generalized = forecast(
            artifact,
            history,
            meal.timestamp,
            composition,
            participant=summary,
            model="generalized",
        )
        assert personalized.prediction_status == "personalized"
        assert generalized.prediction_status == "generalized_fallback"
        assert personalized.horizon_deltas != generalized.horizon_deltas


class TestDrivers:
    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_drivers_are_present_for_the_model_families_that_support_them(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        assert payload["drivers"], "the Ridge family has an exact decomposition"
        for driver in payload["drivers"]:
            assert set(driver) == {"feature", "contribution_mg_dl", "explanation"}

    def test_the_driver_horizon_is_stated(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p001").to_contract_dict()
        joined = " ".join(payload["limitations"])
        assert "Drivers are reported at the" in joined

    def test_a_benchmark_with_no_attribution_returns_an_empty_list(
        self, artifact, repository
    ):
        """Honest emptiness, with a note -- never an invented list."""
        history = repository.get_history("synthetic_p001")
        meal = history.meals[-1]
        result = forecast(
            artifact,
            history,
            meal.timestamp,
            MealComposition.from_meal_record(meal),
            model="persistence",
        )
        assert result.drivers == []
        joined = " ".join(result.limitations).lower()
        assert "no feature attribution" in joined

    def test_persistence_predicts_a_flat_curve_at_the_baseline(
        self, artifact, repository
    ):
        history = repository.get_history("synthetic_p001")
        meal = history.meals[-1]
        result = forecast(
            artifact,
            history,
            meal.timestamp,
            MealComposition.from_meal_record(meal),
            model="persistence",
        )
        values = [point.glucose_mg_dl for point in result.points]
        assert values == pytest.approx([result.baseline_glucose_mg_dl] * 25)
        assert result.summary.incremental_auc_mg_dl_min == 0.0


class TestLimitations:
    @pytest.mark.parametrize("participant_id", ALL_PARTICIPANTS)
    def test_limitations_always_include_the_research_disclaimer(
        self, artifact, repository, participant_id
    ):
        payload = _forecast(artifact, repository, participant_id).to_contract_dict()
        joined = " ".join(payload["limitations"]).lower()
        assert "not medical advice" in joined

    def test_synthetic_provenance_is_disclosed(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p001").to_contract_dict()
        joined = " ".join(payload["limitations"]).lower()
        assert "synthetic development fixtures" in joined
        assert payload["data_source"] == "synthetic_dev"

    def test_missing_modality_is_disclosed(self, artifact, repository):
        """p005 has no heart-rate data; the response must say the inputs were imputed."""
        payload = _forecast(artifact, repository, "synthetic_p005").to_contract_dict()
        joined = " ".join(payload["limitations"])
        assert "hr_mean_30min_bpm" in joined
        assert "median" in joined

    def test_missing_timezone_is_disclosed(self, artifact, repository):
        history = repository.get_history("synthetic_p001")
        meal = history.meals[-1]
        result = forecast(
            artifact,
            history,
            meal.timestamp,
            MealComposition.from_meal_record(meal),
            participant=None,
        )
        joined = " ".join(result.limitations).lower()
        assert "no participant timezone" in joined

    def test_interpolation_caveat_is_always_present(self, artifact, repository):
        payload = _forecast(artifact, repository, "synthetic_p001").to_contract_dict()
        joined = " ".join(payload["limitations"]).lower()
        assert "linearly interpolated" in joined


class TestFailureModes:
    def test_no_premeal_cgm_is_refused(self, artifact):
        history = ParticipantHistory(
            participant_id="p_empty", data_source="synthetic_dev", cgm=[]
        )
        with pytest.raises(InsufficientPremealContext):
            forecast(
                artifact,
                history,
                dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.timezone.utc),
                MealComposition(55.0, 22.0, 16.0, 7.0, 460.0),
            )

    def test_a_stale_baseline_is_refused(self, artifact):
        meal_time = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.timezone.utc)
        history = ParticipantHistory(
            participant_id="p_stale",
            data_source="synthetic_dev",
            cgm=[CgmPoint(meal_time - dt.timedelta(hours=2), 100.0)],
        )
        with pytest.raises(InsufficientPremealContext):
            forecast(
                artifact, history, meal_time, MealComposition(55.0, 22.0, 16.0, 7.0, 460.0)
            )

    def test_a_future_context_override_is_refused(self, artifact, repository):
        history = repository.get_history("synthetic_p001")
        meal = history.meals[-1]
        with pytest.raises(Exception, match="later than meal_timestamp"):
            forecast(
                artifact,
                history,
                meal.timestamp,
                MealComposition.from_meal_record(meal),
                override=ContextOverride(
                    premeal_glucose_mg_dl=300.0,
                    as_of=meal.timestamp + dt.timedelta(minutes=5),
                ),
            )

    def test_an_extreme_meal_is_clipped_and_disclosed(self, artifact, repository):
        """Far-out-of-distribution input must not silently return a bounded curve."""
        history = repository.get_history("synthetic_p002")
        meal = history.meals[-1]
        result = forecast(
            artifact,
            history,
            meal.timestamp,
            MealComposition(
                carbs_g=1000.0,
                protein_g=1000.0,
                fat_g=1000.0,
                fiber_g=0.0,
                calories_kcal=10000.0,
            ),
            participant=repository.get_participant("synthetic_p002"),
        )
        values = [point.glucose_mg_dl for point in result.points[1:]]
        assert max(values) <= artifact.config.model.clip_glucose_max_mg_dl
        if max(values) == artifact.config.model.clip_glucose_max_mg_dl:
            joined = " ".join(result.limitations).lower()
            assert "clipped" in joined
