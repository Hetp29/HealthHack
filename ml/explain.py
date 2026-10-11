"""Model-grounded feature attribution.

The decomposition is exact, not approximate, and that is the point. For a Ridge model
behind a centering scaler:

    predicted_delta = intercept + sum_j ( coef_j * scaled_x_j )

`StandardScaler` centers on the TRAINING mean, so `scaled_x_j` is this meal's
standardized deviation from the average training meal. Therefore:

  * `intercept` is the average excursion the cohort model expects at that horizon --
    the "typical meal response" term;
  * each `coef_j * scaled_x_j` is exactly how many mg/dL this meal's value of feature
    `j` moves the prediction away from that typical response;
  * the terms sum to the prediction with no residual. `verify_decomposition` asserts it.

No language model, no surrogate model, no SHAP approximation, and no hand-written
narrative is involved. If a future model family has no additive decomposition,
`explain` must return an empty driver list and the response must say attribution is
unavailable -- an empty list is honest, a plausible-sounding invented one is a defect.

Three things the output deliberately does NOT claim:
  * **not causality.** A contribution is how this model's fitted coefficients combine
    this meal's features. Observational association, not a measured physiological effect.
  * **not transferability.** Coefficients are shared across the cohort; they describe
    the average relationship in the training data, not this individual's physiology.
  * **not independence.** Macronutrients covary, so correlated features split credit
    between them in ways that reflect the fit, not separable mechanisms.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.config import ATTRIBUTION_CAVEAT, HORIZONS
from ml.features import FEATURE_LABELS

#: Prefix scikit-learn's SimpleImputer(add_indicator=True) gives its indicator columns.
INDICATOR_PREFIX = "missingindicator_"

#: Contributions smaller than this are dropped as noise rather than listed.
MIN_ABS_CONTRIBUTION_MG_DL = 0.25

#: Cap on the number of drivers returned, so the UI shows a readable list.
MAX_DRIVERS = 8


@dataclass(frozen=True)
class DriverContribution:
    """One attribution term, ready to serialize into the API's `drivers`."""

    feature: str
    contribution_mg_dl: float
    explanation: str


def _readable(column: str, horizon: int) -> tuple[str, str]:
    """Map a post-imputation column name to (api_feature_name, explanation)."""
    if column.startswith(INDICATOR_PREFIX):
        base = column[len(INDICATOR_PREFIX) :]
        label = FEATURE_LABELS.get(base, base)
        return (
            f"{base}_unavailable",
            f"Contribution from {label} being unavailable for this meal, at the "
            f"{horizon}-minute horizon. The model treats missingness itself as "
            "information rather than silently filling in an average.",
        )
    label = FEATURE_LABELS.get(column, column)
    return (
        column,
        f"Model-based contribution of {label} at the {horizon}-minute horizon, "
        "relative to the average training meal. " + ATTRIBUTION_CAVEAT,
    )


def explain_horizon(
    generalized,
    features: pd.DataFrame,
    horizon: int,
    *,
    calibration_offset_mg_dl: float = 0.0,
    max_drivers: int = MAX_DRIVERS,
    weight: float = 1.0,
) -> tuple[list[DriverContribution], float, float]:
    """Decompose one prediction at one horizon.

    Args:
        generalized: a fitted `ml.train.GeneralizedModel`.
        features: exactly one row, in the frozen feature order.
        horizon: which horizon to explain.
        calibration_offset_mg_dl: the personalization offset actually applied, surfaced
            as its own driver so the personal correction is visible rather than hidden.
        weight: the share of the served prediction this linear model accounts for.
            1.0 for the pure-Ridge family. For the blend it is the Ridge weight, and
            contributions are scaled by it so each number is the mg/dL that feature
            moves the SERVED prediction -- not the Ridge sub-model's own output. The
            remaining share belongs to the booster and is reported as unexplained.

    Returns:
        (drivers ranked by |contribution|, intercept, summed contributions).
    """
    if len(features) != 1:
        raise ValueError(f"explain_horizon expects exactly one row, got {len(features)}")

    names, coefficients, intercept = generalized.coefficients(horizon)
    scaled = generalized.transform_to_scaled(horizon, features)[0]
    contributions = coefficients * scaled * weight
    intercept = intercept * weight

    drivers: list[DriverContribution] = []
    for column, contribution in zip(names, contributions):
        if abs(contribution) < MIN_ABS_CONTRIBUTION_MG_DL:
            continue
        feature_name, explanation = _readable(str(column), horizon)
        drivers.append(
            DriverContribution(
                feature=feature_name,
                contribution_mg_dl=round(float(contribution), 2),
                explanation=explanation,
            )
        )

    drivers.sort(key=lambda driver: abs(driver.contribution_mg_dl), reverse=True)
    drivers = drivers[:max_drivers]

    if abs(calibration_offset_mg_dl) >= MIN_ABS_CONTRIBUTION_MG_DL:
        drivers.append(
            DriverContribution(
                feature="participant_calibration",
                contribution_mg_dl=round(float(calibration_offset_mg_dl), 2),
                explanation=(
                    "Participant-specific correction at the "
                    f"{horizon}-minute horizon: the shrunk mean residual of the cohort "
                    "model on this participant's earlier training meals only."
                ),
            )
        )
        # Re-rank so the personal term sits in its true position.
        drivers.sort(key=lambda driver: abs(driver.contribution_mg_dl), reverse=True)

    return drivers, float(intercept), float(np.sum(contributions))


def verify_decomposition(
    generalized, features: pd.DataFrame, horizon: int, tolerance: float = 1e-6
) -> tuple[bool, float, float]:
    """Check that intercept + sum(contributions) reproduces the pipeline's prediction.

    Exercised by `tests/test_explain.py`. A failure means the attribution shown to a
    user does not describe the number shown next to it, which is worse than showing
    nothing.
    """
    _, coefficients, intercept = generalized.coefficients(horizon)
    scaled = generalized.transform_to_scaled(horizon, features)[0]
    reconstructed = float(intercept + np.sum(coefficients * scaled))
    predicted = float(generalized.pipelines[horizon].predict(features)[0])
    return abs(reconstructed - predicted) <= tolerance, reconstructed, predicted


def peak_horizon(horizon_deltas: dict[int, float], default: int = 60) -> int:
    """Horizon at which the predicted excursion is largest -- where drivers are reported.

    Drivers describe one horizon, not the whole curve, so the horizon has to be chosen
    and stated. The peak is the clinically interesting moment and the one a user is
    looking at. When nothing rises above baseline, falls back to `default` so the
    response still explains itself.
    """
    positive = {h: d for h, d in horizon_deltas.items() if d > 0}
    if not positive:
        return default if default in HORIZONS else HORIZONS[1]
    return max(positive, key=lambda h: positive[h])
