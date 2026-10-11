"""Generate the illustrative contract example payloads under contracts/examples/.

PROVENANCE WARNING
------------------
These files are SHAPE fixtures. The numbers are illustrative placeholders taken from
the master specification's sample payloads and are deterministically derived here so
that every cross-field invariant holds (25 points, derived horizon read-outs, curve
derived metrics). They are NOT model output and MUST NOT be presented as predictions
or as validation results.

A fixture produced by an actually trained model is written separately to
contracts/examples/predict_response.model_generated.json by `python -m ml.inference
--emit-fixture` once an artifact exists. Neil should bind the UI against the shapes
here and sanity-check against the model-generated file.

Run:  python contracts/build_examples.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "examples"

HORIZONS = (30, 60, 90, 120)
MINUTES = tuple(range(0, 121, 5))

RESEARCH_DISCLAIMER = (
    "Synthetic research demonstration; not medical advice and not for clinical "
    "decision-making."
)
INTERPOLATION_CAVEAT = (
    "Curve shape between the 30/60/90/120-minute learned horizons is linearly "
    "interpolated, so the reported peak time is resolved only to those knots."
)


def interpolate_curve(baseline: float, horizon_values: dict[int, float]) -> list[float]:
    """Piecewise-linear curve through minute 0 (measured baseline) and the horizon knots."""
    knot_minutes = [0, *HORIZONS]
    knot_values = [baseline, *(horizon_values[h] for h in HORIZONS)]
    curve = []
    for minute in MINUTES:
        # Locate the bracketing knot pair.
        idx = max(i for i, km in enumerate(knot_minutes) if km <= minute)
        if idx == len(knot_minutes) - 1:
            curve.append(knot_values[idx])
            continue
        m0, m1 = knot_minutes[idx], knot_minutes[idx + 1]
        v0, v1 = knot_values[idx], knot_values[idx + 1]
        frac = (minute - m0) / (m1 - m0)
        curve.append(round(v0 + frac * (v1 - v0), 1))
    return curve


def curve_metrics(minutes: tuple[int, ...], values: list[float], baseline: float) -> dict:
    """Peak / time-to-peak / iAUC computed from the sampled curve, exactly as frozen."""
    peak = max(values)
    # Earliest-tie peak.
    time_to_peak = next(m for m, v in zip(minutes, values) if v == peak)
    # Five-minute trapezoidal integration of the POSITIVE excursion only.
    iauc = 0.0
    for i in range(len(minutes) - 1):
        left = max(values[i] - baseline, 0.0)
        right = max(values[i + 1] - baseline, 0.0)
        width = minutes[i + 1] - minutes[i]
        iauc += 0.5 * (left + right) * width
    return {
        "peak_mg_dl": round(peak, 1),
        "peak_delta_mg_dl": round(peak - baseline, 1),
        "time_to_peak_minutes": time_to_peak,
        "incremental_auc_mg_dl_min": round(iauc, 1),
    }


def forecast(
    participant_id: str,
    baseline: float,
    horizon_values: dict[int, float],
    prediction_status: str,
    drivers: list[dict],
    limitations: list[str],
) -> dict:
    values = interpolate_curve(baseline, horizon_values)
    points = [
        {
            "minute": minute,
            "glucose_mg_dl": value,
            "lower_mg_dl": None,
            "upper_mg_dl": None,
        }
        for minute, value in zip(MINUTES, values)
    ]
    by_minute = dict(zip(MINUTES, values))
    return {
        "participant_id": participant_id,
        "meal_timestamp": "2026-10-08T12:30:00Z",
        "model_version": "v0.1.0",
        "data_source": "synthetic_dev",
        "prediction_status": prediction_status,
        "points": points,
        # Derived from points by exact minute lookup -- never independently invented.
        "glucose_at_minutes": {str(h): by_minute[h] for h in HORIZONS},
        "metrics": curve_metrics(MINUTES, values, baseline),
        "drivers": drivers,
        "uncertainty_status": "not_calibrated",
        "limitations": limitations,
    }


def write(name: str, payload: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"  wrote {name}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    write(
        "predict_request.json",
        {
            "participant_id": "synthetic_p001",
            "meal_timestamp": "2026-10-08T12:30:00Z",
            "meal": {
                "carbs_g": 55,
                "protein_g": 22,
                "fat_g": 16,
                "fiber_g": 7,
                "calories_kcal": 460,
            },
            "context_override": None,
        },
    )

    personalized_drivers = [
        {
            "feature": "carbs_g",
            "contribution_mg_dl": 12.5,
            "explanation": "Model-based contribution at the peak horizon; association within the model, not a causal physiological effect.",
        },
        {
            "feature": "premeal_glucose_mg_dl",
            "contribution_mg_dl": -4.2,
            "explanation": "Model-based contribution at the peak horizon; association within the model, not a causal physiological effect.",
        },
        {
            "feature": "participant_calibration",
            "contribution_mg_dl": 3.1,
            "explanation": "Shrunk participant-specific residual offset learned from this participant's earlier training meals only.",
        },
    ]

    write(
        "predict_response.json",
        forecast(
            participant_id="synthetic_p001",
            baseline=105.0,
            horizon_values={30: 124.0, 60: 146.0, 90: 139.0, 120: 118.0},
            prediction_status="personalized",
            drivers=personalized_drivers,
            limitations=[RESEARCH_DISCLAIMER, INTERPOLATION_CAVEAT],
        ),
    )

    write(
        "predict_response_generalized_fallback.json",
        forecast(
            participant_id="synthetic_p006",
            baseline=98.0,
            horizon_values={30: 118.0, 60: 131.0, 90: 122.0, 120: 106.0},
            prediction_status="generalized_fallback",
            drivers=[
                {
                    "feature": "carbs_g",
                    "contribution_mg_dl": 11.8,
                    "explanation": "Model-based contribution at the peak horizon; association within the model, not a causal physiological effect.",
                }
            ],
            limitations=[
                "Generalized cohort model used: this participant has 1 usable training meal episode, below the configured minimum of 4, so no participant-specific calibration was applied.",
                RESEARCH_DISCLAIMER,
                INTERPOLATION_CAVEAT,
            ],
        ),
    )

    write(
        "participants_response.json",
        {
            "participants": [
                {
                    "participant_id": "synthetic_p001",
                    "display_name": "Participant 001",
                    "data_source": "synthetic_dev",
                    "days_available": 7.0,
                },
                {
                    "participant_id": "synthetic_p002",
                    "display_name": "Participant 002",
                    "data_source": "synthetic_dev",
                    "days_available": 7.0,
                },
                {
                    "participant_id": "synthetic_p006",
                    "display_name": "Participant 006",
                    "data_source": "synthetic_dev",
                    "days_available": 6.83,
                },
            ]
        },
    )

    write(
        "history_response.json",
        {
            "participant_id": "synthetic_p001",
            # Truncated illustrative window. A real response carries ~2016 CGM points per week.
            "cgm": [
                {"timestamp": "2026-10-08T12:15:00Z", "glucose_mg_dl": 101.0},
                {"timestamp": "2026-10-08T12:20:00Z", "glucose_mg_dl": 103.0},
                {"timestamp": "2026-10-08T12:25:00Z", "glucose_mg_dl": 104.0},
                {"timestamp": "2026-10-08T12:30:00Z", "glucose_mg_dl": 105.0},
                {"timestamp": "2026-10-08T12:35:00Z", "glucose_mg_dl": 109.0},
            ],
            "meals": [
                {
                    "meal_id": "synthetic_p001_m017",
                    "participant_id": "synthetic_p001",
                    "timestamp": "2026-10-08T12:30:00Z",
                    "carbs_g": 55.0,
                    "protein_g": 22.0,
                    "fat_g": 16.0,
                    "fiber_g": 7.0,
                    "calories_kcal": 460.0,
                    "image_ref": None,
                }
            ],
            "heart_rate": [
                {
                    "timestamp": "2026-10-08T12:25:00Z",
                    "heart_rate_bpm": 68.0,
                    "resting_heart_rate_bpm": 61.0,
                },
                {
                    "timestamp": "2026-10-08T12:30:00Z",
                    "heart_rate_bpm": 71.0,
                    "resting_heart_rate_bpm": 61.0,
                },
            ],
        },
    )

    # Metrics example with every numeric slot null: this is the shape the API returns
    # BEFORE `python -m ml.evaluate` has been run. Neil renders the "no validated
    # metrics" state from exactly this.
    write(
        "metrics_response.json",
        {
            "dataset_source": "synthetic_dev",
            "evaluation_protocol": {
                "name": "within_person_chronological",
                "description": (
                    "Per participant, meal episodes are ordered by timestamp; the earliest "
                    "75% train and the latest 25% are held out. Splits are by whole meal "
                    "episode. Five-minute CGM rows are never randomly split. Imputers, "
                    "scalers and participant calibration are fitted on the training fold only."
                ),
                "split_unit": "meal_episode",
                "test_fraction": 0.25,
                "evaluated_at": "2026-10-10T00:00:00Z",
                "excluded_overlapping_meals": False,
            },
            "by_model": [],
            "by_horizon": [],
            "sample_counts": {
                "participants": 0,
                "train_episodes": 0,
                "test_episodes": 0,
                "excluded_episodes": None,
                "by_participant": [],
            },
            "limitations": [
                "No evaluation has been run against this artifact, so no validated metrics exist.",
                "Run `python -m ml.evaluate --protocol within_person_chronological` to populate this report.",
            ],
        },
    )

    write(
        "model_info_response.json",
        {
            "model_version": "v0.1.0",
            "trained_at": "2026-10-10T00:00:00Z",
            "data_source": "synthetic_dev",
            "feature_set_version": "fs-1.0",
            "metrics_available": False,
            "limitations": [
                RESEARCH_DISCLAIMER,
                "Trained on synthetic development fixtures, not organizer data.",
                "Uncertainty intervals are not calibrated; no interval bounds are emitted.",
            ],
        },
    )

    write(
        "error_response.json",
        {
            "code": "participant_not_found",
            "message": "Unknown participant identifier.",
            "detail": {"participant_id": "nope_p999"},
        },
    )

    write(
        "health_response.json",
        {"status": "ok", "version": "1.0.0"},
    )

    write(
        "compare_request.json",
        {
            "participant_ids": ["synthetic_p001", "synthetic_p002"],
            "meal_timestamp": "2026-10-08T12:30:00Z",
            "meal": {
                "carbs_g": 55,
                "protein_g": 22,
                "fat_g": 16,
                "fiber_g": 7,
                "calories_kcal": 460,
            },
            "context_override": None,
        },
    )


if __name__ == "__main__":
    print("generating contract v1.0 example payloads:")
    main()
