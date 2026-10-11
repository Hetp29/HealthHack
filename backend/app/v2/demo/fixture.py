"""A labelled TEST-FIXTURE bundle: generated arithmetic, not anyone's glucose.

Used by the automated tests and by the published example payloads, so the replay
machinery can be exercised on any machine without research data. Its `data_class` is
`test_fixture`, which the replay stores under the `demo` label -- it can never be
stored, shown or described as research or device data, and the real demonstration
does not use it.
"""

from __future__ import annotations

import datetime as dt
import math

from backend.app.v2.demo import source

UTC = dt.timezone.utc
FIRST_MEAL = dt.datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

#: Hours after the first meal. Meals 2 and 3 are 20 minutes apart, so meal 2 has an
#: intervening meal and is ineligible -- the exclusion case.
MEAL_OFFSETS_HOURS = (0.0, 5.0, 5.0 + 20 / 60, 10.0, 21.0, 26.0, 31.0, 45.0, 50.0)


def _value(moment: dt.datetime, meals: list[dt.datetime]) -> float:
    minutes = moment.hour * 60 + moment.minute
    value = 102.0 + 9.0 * math.sin(2 * math.pi * minutes / 1440.0)
    for meal in meals:
        elapsed = (moment - meal).total_seconds() / 60.0
        if 0 < elapsed < 180:
            value += 48.0 * math.exp(-((elapsed - 55.0) / 34.0) ** 2)
    return round(value, 1)


def fixture_payload(*, alert_threshold_mg_dl: float = 130.0, shift_after: dt.datetime | None = None,
                    shift_mg_dl: float = 0.0, display_from_meal: int = 1) -> dict:
    """`display_from_meal > 1` marks the earlier meals as backfill (returning-user mode).

    `shift_after`/`shift_mg_dl` produce a variant whose readings differ only AFTER
    an instant -- used to prove that later data cannot change an earlier forecast."""
    meals = [FIRST_MEAL + dt.timedelta(hours=h) for h in MEAL_OFFSETS_HOURS]
    start, end = FIRST_MEAL - dt.timedelta(days=2), meals[-1] + dt.timedelta(minutes=180)
    cgm, moment = [], start
    while moment <= end:
        value = _value(moment, meals)
        if shift_after is not None and moment > shift_after:
            value = round(value + shift_mg_dl, 1)
        cgm.append((moment, value))
        moment += dt.timedelta(minutes=5)
    meal_rows = [(f"fixture_meal_{i + 1:02d}", at,
                  {"carbs_g": 30.0 + 9.0 * i, "protein_g": 22.0, "fat_g": 16.0, "fiber_g": 7.0,
                   "calories_kcal": 380.0 + 36.0 * i}) for i, at in enumerate(meals)]
    times = [t for t, _ in cgm]
    assessed = source.assess_meals(times, meal_rows, meals)
    plan = source.plan_window(assessed)
    return source.make_payload(
        scenario_id=("test-fixture-replay-v1" if display_from_meal == 1
                     else "test-fixture-returning-user-replay-v1"),
        data_class="test_fixture", timezone="UTC",
        device_id="fixture-sensor",
        source={"dataset": "generated test fixture", "participant": "none",
                "plan": {"activation_meal": plan["activation_index"] + 1,
                         "final_meal": len(meal_rows),
                         "alert_threshold_mg_dl": alert_threshold_mg_dl,
                         **({} if display_from_meal == 1 else {
                             "display_from_meal": display_from_meal,
                             "backfill_fit_note": "Generated fixture; no model-fit relationship."})}},
        cgm=cgm, meals=meal_rows)


def fixture_bundle(**kwargs) -> source.Bundle:
    return source.bundle_from_payload(fixture_payload(**kwargs))
