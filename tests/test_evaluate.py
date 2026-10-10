"""Evaluation protocol correctness and report conformance."""

from __future__ import annotations

import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devfixture import SyntheticDevRepository
from ml.evaluate import MODEL_NAMES, PROTOCOLS, evaluate

SCHEMA_PATH = pathlib.Path("contracts/schemas/metrics_response.schema.json")


@pytest.fixture(scope="module")
def repository():
    return SyntheticDevRepository()


@pytest.fixture(scope="module")
def within_person(repository):
    return evaluate(repository, DEFAULT_CONFIG, protocol="within_person_chronological")


@pytest.fixture(scope="module")
def cohort(repository):
    return evaluate(repository, DEFAULT_CONFIG, protocol="cohort_holdout")


class TestReportConformance:
    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_report_validates_against_the_frozen_schema(self, repository, protocol):
        report, _ = evaluate(repository, DEFAULT_CONFIG, protocol=protocol)
        validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
        errors = sorted(validator.iter_errors(report), key=lambda e: list(e.path))
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)

    def test_debug_detail_is_kept_out_of_the_report(self, within_person):
        """Extra keys would break `additionalProperties: false` on the contract."""
        report, detail = within_person
        assert not [key for key in report if key.startswith("_")]
        assert "exclusions" in detail
        assert "personalization_model" in detail

    def test_report_is_json_serializable(self, within_person):
        report, detail = within_person
        json.dumps(report)
        json.dumps(detail)

    def test_unknown_protocol_is_rejected(self, repository):
        with pytest.raises(ValueError, match="unknown protocol"):
            evaluate(repository, DEFAULT_CONFIG, protocol="random_split")


class TestProtocolMetadata:
    def test_split_unit_is_always_the_meal_episode(self, within_person, cohort):
        for report, _ in (within_person, cohort):
            assert report["evaluation_protocol"]["split_unit"] == "meal_episode"

    def test_protocol_name_is_reported(self, within_person, cohort):
        assert (
            within_person[0]["evaluation_protocol"]["name"]
            == "within_person_chronological"
        )
        assert cohort[0]["evaluation_protocol"]["name"] == "cohort_holdout"

    def test_description_states_the_fold_discipline(self, within_person):
        description = within_person[0]["evaluation_protocol"]["description"]
        assert "training fold only" in description
        assert "never split" in description

    def test_evaluated_at_is_offset_aware(self, within_person):
        assert within_person[0]["evaluation_protocol"]["evaluated_at"].endswith("Z")

    def test_data_source_is_labelled(self, within_person):
        assert within_person[0]["dataset_source"] == "synthetic_dev"


class TestMeasuredResults:
    def test_all_four_models_are_scored(self, within_person):
        names = [block["model_name"] for block in within_person[0]["by_model"]]
        assert names == list(MODEL_NAMES)

    def test_every_horizon_is_reported_with_a_sample_count(self, within_person):
        for block in within_person[0]["by_model"]:
            horizons = [row["horizon_minutes"] for row in block["by_horizon"]]
            assert horizons == list(HORIZONS)
            for row in block["by_horizon"]:
                assert row["n"] > 0
                assert row["mae_mg_dl"] is not None

    def test_by_horizon_mirrors_the_personalized_block(self, within_person):
        report = within_person[0]
        personalized = next(
            block for block in report["by_model"] if block["model_name"] == "personalized"
        )
        assert report["by_horizon"] == personalized["by_horizon"]

    def test_rmse_is_never_below_mae(self, within_person):
        for block in within_person[0]["by_model"]:
            assert block["overall_rmse_mg_dl"] >= block["overall_mae_mg_dl"] - 1e-9

    def test_the_model_beats_the_persistence_baseline(self, within_person):
        """If this fails, the model has not learned meal response at all."""
        blocks = {b["model_name"]: b for b in within_person[0]["by_model"]}
        assert (
            blocks["generalized"]["overall_mae_mg_dl"]
            < blocks["persistence"]["overall_mae_mg_dl"]
        )

    def test_the_model_also_beats_the_cohort_mean_benchmark(self, within_person):
        """Beating persistence alone would only show that meals raise glucose."""
        blocks = {b["model_name"]: b for b in within_person[0]["by_model"]}
        assert (
            blocks["generalized"]["overall_mae_mg_dl"]
            < blocks["cohort_mean_delta"]["overall_mae_mg_dl"]
        )

    def test_curve_metrics_are_reported_with_their_own_sample_count(self, within_person):
        for block in within_person[0]["by_model"]:
            curve = block["curve"]
            assert curve["n"] > 0
            assert curve["peak_mae_mg_dl"] is not None
            assert curve["time_to_peak_mae_minutes"] is not None
            assert curve["iauc_mae_mg_dl_min"] is not None

    def test_interval_coverage_is_null_while_uncalibrated(self, within_person):
        for block in within_person[0]["by_model"]:
            for row in block["by_horizon"]:
                assert row["interval_coverage"] is None


class TestSampleCounts:
    def test_counts_are_internally_consistent(self, within_person):
        counts = within_person[0]["sample_counts"]
        assert counts["train_episodes"] == sum(
            row["train_episodes"] for row in counts["by_participant"]
        )
        assert counts["test_episodes"] == sum(
            row["test_episodes"] for row in counts["by_participant"]
        )
        assert counts["participants"] == len(counts["by_participant"])

    def test_every_participant_is_listed(self, within_person, repository):
        listed = {row["participant_id"] for row in within_person[0]["sample_counts"]["by_participant"]}
        assert listed == {s.participant_id for s in repository.list_participants()}

    def test_personalization_flag_is_recorded_per_participant(self, within_person):
        rows = within_person[0]["sample_counts"]["by_participant"]
        assert any(row["personalized"] for row in rows)
        sparse = next(r for r in rows if r["participant_id"] == "synthetic_p006")
        assert sparse["personalized"] is False
        assert sparse["test_episodes"] == 0

    def test_excluded_episodes_are_reported(self, within_person):
        assert within_person[0]["sample_counts"]["excluded_episodes"] is not None


class TestPersonalizationComparison:
    def test_comparison_is_present_and_adds_up(self, within_person):
        comparison = within_person[0]["personalization"]
        total = (
            comparison["participants_improved"]
            + comparison["participants_worsened"]
            + comparison["participants_unchanged"]
        )
        assert total == len(comparison["by_participant"])

    def test_delta_is_generalized_minus_personalized(self, within_person):
        for row in within_person[0]["personalization"]["by_participant"]:
            if row["delta_mae_mg_dl"] is None:
                continue
            assert row["delta_mae_mg_dl"] == pytest.approx(
                row["generalized_mae_mg_dl"] - row["personalized_mae_mg_dl"], abs=0.011
            )

    def test_a_negative_result_would_be_reported_not_hidden(self, within_person):
        """The schema and the loop both allow `worsened` to be non-zero."""
        comparison = within_person[0]["personalization"]
        assert "participants_worsened" in comparison
        assert comparison["participants_worsened"] >= 0

    def test_cohort_holdout_personalization_is_identical_by_construction(self, cohort):
        report = cohort[0]
        blocks = {b["model_name"]: b for b in report["by_model"]}
        assert (
            blocks["personalized"]["overall_mae_mg_dl"]
            == blocks["generalized"]["overall_mae_mg_dl"]
        )
        for row in report["personalization"]["by_participant"]:
            assert row["delta_mae_mg_dl"] == pytest.approx(0.0, abs=1e-9)

    def test_cohort_holdout_says_so_in_its_limitations(self, cohort):
        joined = " ".join(cohort[0]["limitations"]).lower()
        assert "cold-start" in joined
        assert "identical by construction" in joined


class TestLimitations:
    def test_limitations_are_never_empty(self, within_person, cohort):
        for report, _ in (within_person, cohort):
            assert report["limitations"]

    def test_synthetic_provenance_is_disclosed(self, within_person):
        joined = " ".join(within_person[0]["limitations"]).lower()
        assert "synthetic" in joined
        assert "not" in joined

    def test_interpolation_limit_is_disclosed(self, within_person):
        joined = " ".join(within_person[0]["limitations"]).lower()
        assert "piecewise-linear" in joined or "knot" in joined

    def test_uncalibrated_intervals_are_disclosed(self, within_person):
        joined = " ".join(within_person[0]["limitations"]).lower()
        assert "interval" in joined


class TestOverlapSensitivity:
    def test_excluding_overlapping_meals_is_recorded_and_changes_counts(self, repository):
        included, _ = evaluate(
            repository, DEFAULT_CONFIG, protocol="within_person_chronological"
        )
        excluded, _ = evaluate(
            repository,
            DEFAULT_CONFIG,
            protocol="within_person_chronological",
            exclude_overlapping=True,
        )
        assert included["evaluation_protocol"]["excluded_overlapping_meals"] is False
        assert excluded["evaluation_protocol"]["excluded_overlapping_meals"] is True
        total_included = (
            included["sample_counts"]["train_episodes"]
            + included["sample_counts"]["test_episodes"]
        )
        total_excluded = (
            excluded["sample_counts"]["train_episodes"]
            + excluded["sample_counts"]["test_episodes"]
        )
        assert total_excluded < total_included
