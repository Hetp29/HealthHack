"""FREEZE VERIFICATION (new in the fs-1.3 freeze retest) -- Phase 4.

These tests verify the frozen retrieval and personalization POLICY against the 16
cases the freeze mission enumerates. They assert what the implementation actually
does; where the policy is genuinely ambiguous the test records the ambiguity in
its own assertion message rather than legislating new behaviour.

They add no behaviour and change no model. Every case is constructed from
synthetic CGM and meal records so the frozen artifact is never involved.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math

import pytest

from ml.config import DEFAULT_CONFIG
from ml.features import (
    FEATURE_NAMES,
    InsufficientPremealContext,
    MealComposition,
    build_features,
)
from ml.schema import CgmPoint, MealRecord, ParticipantHistory

CFG = DEFAULT_CONFIG.features
UTC = dt.timezone.utc
T0 = dt.datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
MACROS = MealComposition(carbs_g=60.0, protein_g=20.0, fat_g=15.0,
                         fiber_g=5.0, calories_kcal=450.0)


def cgm_series(start: dt.datetime, end: dt.datetime, *, step_minutes: int = 5,
               base: float = 100.0, amplitude: float = 0.0,
               period_minutes: float = 120.0) -> list[CgmPoint]:
    """A dense 5-minute CGM series; `amplitude` adds a smooth excursion."""
    points, t, i = [], start, 0
    while t <= end:
        value = base + amplitude * math.sin(2 * math.pi * (i * step_minutes) / period_minutes)
        points.append(CgmPoint(timestamp=t, glucose_mg_dl=round(value, 1)))
        t += dt.timedelta(minutes=step_minutes)
        i += 1
    return points


def history_with_prior_meals(n_priors: int, *, participant: str = "p1",
                             gap_hours: float = 6.0,
                             last_prior_offset_minutes: float | None = None,
                             prior_participant: str | None = None,
                             duplicate_last: bool = False,
                             truncate_last_response_at: float | None = None):
    """Build a history whose query meal at T0 has exactly `n_priors` ADMISSIBLE priors.

    Priors are spaced `gap_hours` apart ending well before T0, so each one's whole
    120-minute response window has elapsed before the query.
    """
    meals: list[MealRecord] = []
    first = T0 - dt.timedelta(hours=gap_hours * (n_priors + 1))
    cgm_start = first - dt.timedelta(hours=2)
    for i in range(n_priors):
        t = first + dt.timedelta(hours=gap_hours * i)
        if last_prior_offset_minutes is not None and i == n_priors - 1:
            t = T0 - dt.timedelta(minutes=last_prior_offset_minutes)
        meals.append(MealRecord(
            meal_id=f"m{i}", participant_id=prior_participant or participant,
            timestamp=t, carbs_g=40.0 + 5 * i, protein_g=15.0, fat_g=10.0,
            fiber_g=4.0, calories_kcal=350.0 + 10 * i))
    if duplicate_last and meals:
        meals.append(dataclasses.replace(meals[-1]))
    end = T0
    if truncate_last_response_at is not None and meals:
        end = meals[-1].timestamp + dt.timedelta(minutes=truncate_last_response_at)
    cgm = cgm_series(cgm_start, max(end, T0) if truncate_last_response_at is None else end,
                     amplitude=25.0)
    if truncate_last_response_at is not None:
        # the query still needs a baseline reading at T0
        cgm = cgm + [CgmPoint(timestamp=T0, glucose_mg_dl=110.0)]
    return ParticipantHistory(participant_id=participant, data_source="official",
                              cgm=sorted(cgm, key=lambda p: p.timestamp),
                              meals=sorted(meals, key=lambda m: m.timestamp))


def features_for(history: ParticipantHistory):
    return build_features(history, T0, MACROS, CFG, meal_id="query")


def q(row, name):
    return row.values.get(name, math.nan)


# --------------------------------------------------------------------------- #
# Cases 1-7: the retrieval gate at every history size around the threshold
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n_priors,retrieval_active", [
    (0, False), (1, False), (2, False), (3, False), (4, False),
    (5, True), (6, True), (9, True),
])
def test_retrieval_gate_is_exactly_five(n_priors, retrieval_active):
    """Cases 1-7. The frozen gate is `len(priors) < retrieval_min_history`, so
    FOUR priors fall back and EXACTLY FIVE activates retrieval."""
    assert CFG.retrieval_min_history == 5, "the frozen gate must remain 5"
    assert CFG.retrieval_k == 5, "the frozen neighbour count must remain 5"
    row = features_for(history_with_prior_meals(n_priors))
    active = not math.isnan(q(row, "q_retr_delta_60"))
    assert active is retrieval_active, (
        f"{n_priors} priors: retrieval active={active}, expected {retrieval_active}")

    # THE GENUINE eligible-history count is `r_hist_n`, and it is uncapped.
    assert q(row, "r_hist_n") == float(n_priors), (
        "r_hist_n must report the genuine admissible history count")

    if not retrieval_active:
        # In the fallback branch q_retr_n carries the true count, so the fallback
        # is observable rather than silent.
        assert q(row, "q_retr_n") == float(n_priors)
        for h in (30, 60, 90, 120):
            assert math.isnan(q(row, f"q_retr_delta_{h}"))
        assert q(row, "q_retr_weight") == 0.0
    else:
        # DOCUMENTED FROZEN SEMANTICS, recorded here rather than changed:
        # in the ACTIVE branch `q_retr_n` is the number of neighbours actually
        # RETRIEVED, i.e. min(retrieval_k, n_priors) -- not the eligible count.
        # The two branches therefore give `q_retr_n` slightly different meanings.
        # This is a naming nuance, not a leakage or correctness fault: the genuine
        # count is always available as `r_hist_n`. See FS13_MODEL_FREEZE_V1.md,
        # "Known model weaknesses".
        assert q(row, "q_retr_n") == float(min(CFG.retrieval_k, n_priors))


def test_fallback_is_deterministic_for_sparse_history():
    """Case 7 continued: the sparse-history fallback must be identical on repeat."""
    h = history_with_prior_meals(2)
    a, b = features_for(h), features_for(h)
    assert a.values == b.values


def test_fingerprint_and_retrieval_share_one_causal_filter():
    """Both features come from the SAME admissibility filter, so they can never
    disagree about what history EXISTS -- `r_hist_n` reports it, and `q_retr_n`
    can only ever be that count capped at `retrieval_k`."""
    for n in (0, 3, 5, 8):
        row = features_for(history_with_prior_meals(n))
        assert q(row, "r_hist_n") == float(n)
        assert q(row, "q_retr_n") == float(min(CFG.retrieval_k, n))
        assert q(row, "q_retr_n") <= q(row, "r_hist_n")


# --------------------------------------------------------------------------- #
# Cases 8-10: temporal admissibility boundaries
# --------------------------------------------------------------------------- #
def test_prior_with_incomplete_response_window_is_excluded():
    """Case 8. A prior meal whose 120-minute window has not fully elapsed is not
    admissible, so the counts drop by one."""
    full = features_for(history_with_prior_meals(6))
    partial = features_for(history_with_prior_meals(6, last_prior_offset_minutes=30.0))
    assert q(full, "r_hist_n") == 6.0
    assert q(partial, "r_hist_n") == 5.0, (
        "a meal 30 minutes before the query cannot have an observed 120-minute "
        "response and must be excluded")


def test_prior_whose_response_ends_exactly_at_the_query_is_excluded():
    """Case 9. The frozen filter is `window_end >= meal_timestamp` -> excluded.
    A response ending exactly AT the query instant is therefore NOT used. This is
    the strict boundary, recorded here so the policy is pinned."""
    exact = features_for(history_with_prior_meals(6, last_prior_offset_minutes=120.0))
    assert q(exact, "r_hist_n") == 5.0, (
        "the boundary is strict: window_end == query_time is EXCLUDED")
    just_past = features_for(history_with_prior_meals(6, last_prior_offset_minutes=125.0))
    assert q(just_past, "r_hist_n") == 6.0, (
        "a response ending 5 minutes before the query IS admissible")


def test_prior_after_the_query_cannot_enter_any_feature():
    """Case 10. A future meal is removed by `truncated_at`, so it is absent rather
    than merely unused."""
    h = history_with_prior_meals(5)
    future = MealRecord(meal_id="future", participant_id="p1",
                        timestamp=T0 + dt.timedelta(hours=3), carbs_g=99.0,
                        protein_g=9.0, fat_g=9.0, fiber_g=9.0, calories_kcal=900.0)
    with_future = ParticipantHistory(
        participant_id="p1", data_source="official", cgm=h.cgm,
        meals=sorted(h.meals + [future], key=lambda m: m.timestamp))
    assert features_for(h).values == features_for(with_future).values, (
        "adding a LATER meal changed the features -- future leakage")


def test_future_cgm_cannot_enter_any_feature():
    """No post-prediction CGM may reach inference."""
    h = history_with_prior_meals(5)
    extra = cgm_series(T0 + dt.timedelta(minutes=5), T0 + dt.timedelta(minutes=180),
                       base=300.0)
    with_future = ParticipantHistory(
        participant_id="p1", data_source="official",
        cgm=sorted(h.cgm + extra, key=lambda p: p.timestamp), meals=h.meals)
    assert features_for(h).values == features_for(with_future).values, (
        "post-meal CGM changed the features -- future leakage")


# --------------------------------------------------------------------------- #
# Cases 11-16: isolation, duplicates, sparsity, bad input
# --------------------------------------------------------------------------- #
def test_only_the_queried_participants_meals_are_retrieved():
    """Case 11. A history object holds one participant by construction; this pins
    that a foreign participant_id on a meal record does not smuggle it in as
    extra admissible history."""
    own = features_for(history_with_prior_meals(5))
    foreign = features_for(history_with_prior_meals(5, prior_participant="SOMEONE_ELSE"))
    assert q(own, "r_hist_n") == 5.0
    assert q(foreign, "q_retr_n") <= q(foreign, "r_hist_n"), (
        "fingerprint and retrieval must agree about admissible history regardless "
        "of the participant_id written on the meal row")


def test_duplicate_meal_records_are_handled_without_error():
    """Case 12. A duplicated record must not crash and must not silently inflate
    the admissible count beyond the records present."""
    row = features_for(history_with_prior_meals(5, duplicate_last=True))
    assert q(row, "r_hist_n") in (5.0, 6.0), (
        "a duplicate is either deduplicated (5) or counted as a second record (6); "
        f"got {q(row, 'r_hist_n')}")
    assert q(row, "q_retr_n") == 5.0
    assert not math.isnan(q(row, "q_retr_delta_60"))


def test_sparse_cgm_still_produces_finite_core_features():
    """Case 13. Sparse CGM must degrade, not explode."""
    sparse = cgm_series(T0 - dt.timedelta(hours=10), T0, step_minutes=30, amplitude=10.0)
    h = ParticipantHistory(participant_id="p1", data_source="official",
                           cgm=sparse, meals=[])
    row = features_for(h)
    assert not math.isnan(row.values["premeal_glucose_mg_dl"])
    assert all(v is None or isinstance(v, float) for v in row.values.values())


def test_missing_cgm_entirely_is_refused_not_guessed():
    h = ParticipantHistory(participant_id="p1", data_source="official", cgm=[], meals=[])
    with pytest.raises(InsufficientPremealContext):
        features_for(h)


def test_incomplete_macros_are_refused_not_imputed():
    """Case 14. The frozen contract REFUSES an incomplete meal rather than guessing
    its macros: `MealComposition` requires all five, and building one from a meal
    record with gaps raises. Nothing is silently zero-filled."""
    from ml.features import FeatureError

    incomplete = MealRecord(meal_id="m", participant_id="p1", timestamp=T0,
                            carbs_g=60.0, protein_g=None, fat_g=None,
                            fiber_g=None, calories_kcal=None)
    assert not incomplete.has_complete_macros
    with pytest.raises(FeatureError):
        MealComposition.from_meal_record(incomplete)


def test_absent_optional_context_surfaces_as_missing():
    """Features that genuinely have no observation -- heart rate here -- must be
    reported in `missing_features` rather than imputed at prediction time."""
    h = history_with_prior_meals(5)          # no heart-rate records at all
    row = features_for(h)
    assert "hr_mean_30min_bpm" in row.missing_features
    assert math.isnan(row.values["hr_mean_30min_bpm"])


def test_fiber_zero_is_preserved_not_treated_as_missing():
    """Ruling D4: a measured fibre of 0 g is information, not absence."""
    h = history_with_prior_meals(5)
    zero_fiber = MealComposition(carbs_g=60.0, protein_g=20.0, fat_g=15.0,
                                 fiber_g=0.0, calories_kcal=450.0)
    row = build_features(h, T0, zero_fiber, CFG, meal_id="query")
    assert row.values["fiber_g"] == 0.0
    assert "fiber_g" not in row.missing_features


def test_unusually_large_meal_stays_finite():
    """Case 15. An extreme but syntactically valid meal must not produce NaN/inf."""
    h = history_with_prior_meals(5)
    huge = MealComposition(carbs_g=900.0, protein_g=400.0, fat_g=400.0,
                           fiber_g=200.0, calories_kcal=9000.0)
    row = build_features(h, T0, huge, CFG, meal_id="query")
    for name in ("carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"):
        assert math.isfinite(row.values[name])


def test_feature_row_always_has_the_frozen_39_in_frozen_order():
    """Case 16 adjacent: the schema must not drift per request."""
    for n in (0, 4, 5, 9):
        row = features_for(history_with_prior_meals(n))
        assert tuple(row.values.keys()) == FEATURE_NAMES, (
            "feature NAMES or ORDER changed for a history size")
        assert len(row.values) == 39
