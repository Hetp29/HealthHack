"""TEMPORARY development fixture -- NOT the canonical data layer.

==============================================================================
 THIS IS A SCAFFOLD. IT EXISTS ONLY SO THE MODELLING PIPELINE CAN BE BUILT AND
 TESTED BEFORE HET'S CANONICAL DATA LAYER AND THE ORGANIZER DATASET ARRIVE.
==============================================================================

What this is:
  * a deterministic in-memory generator of contract-shaped canonical records,
  * labelled `data_source="synthetic_dev"` on every record and every forecast
    derived from it, so the provenance is always visible in the UI,
  * an implementation of the same read interface the backend expects from Het's
    `DataRepository`, so swapping in the real layer is a one-line binding change.

What this is explicitly NOT:
  * a competing ingestion pipeline. It performs no file parsing, no validation,
    no quarantine, no checksums, no persistence. Those are Het's (`data/**`).
  * a physiological model. The response kernel below is a smooth plausible shape
    chosen to exercise the pipeline. NOTHING learned from this fixture may be
    presented as physiology, as a validation result, or as evidence that the
    model works. Metrics computed on it are pipeline self-consistency checks.

Retirement plan: once `data/` lands, set `GLUCOTWIN_DATA_BACKEND=sqlite`. This
module then survives only as a test fixture. Delete it before submission if the
official dataset covers the test cases.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

import numpy as np

from ml.schema import (
    ActivityEvent,
    CgmPoint,
    HeartRatePoint,
    MealRecord,
    MedicationEvent,
    ParticipantHistory,
    ParticipantSummary,
)

DATA_SOURCE = "synthetic_dev"

#: Fixed epoch so the fixture is byte-identical across runs and machines.
EPOCH = dt.datetime(2026, 10, 1, 0, 0, tzinfo=dt.timezone.utc)

CGM_CADENCE_MINUTES = 5
HR_CADENCE_MINUTES = 5


@dataclass(frozen=True)
class SyntheticTraits:
    """Latent per-participant traits driving the generated signals.

    These are generator inputs, never model inputs. The model never sees them; it
    must infer response behaviour from observable history alone.
    """

    participant_id: str
    display_name: str
    fasting_glucose: float          # mg/dL overnight level
    carb_sensitivity: float         # mg/dL peak rise per gram of net carbohydrate
    peak_time_base: float           # minutes to peak for a carb-only meal
    fat_delay_per_g: float          # minutes added to peak time per gram of fat
    protein_rise_per_g: float       # mg/dL added per gram of protein
    fiber_damping: float            # fractional peak reduction per gram of fiber
    clearance_shape: float          # >1 sharpens the decay after the peak
    cgm_noise_sd: float             # AR(1) innovation SD, mg/dL
    dawn_amplitude: float           # mg/dL circadian swing
    resting_hr: float               # bpm
    meals_per_day: int
    has_heart_rate: bool
    medication_class: str | None
    #: Day indices on which this participant logged meals. `None` means every day.
    #: Used to produce a genuinely sparse logger for the fallback path.
    meal_days: tuple[int, ...] | None = None


#: Six participants with deliberately heterogeneous response behaviour, so that
#: personalization has something real to find and the fallback path is exercised.
TRAITS: tuple[SyntheticTraits, ...] = (
    SyntheticTraits(
        participant_id="synthetic_p001",
        display_name="Participant 001",
        fasting_glucose=96.0,
        carb_sensitivity=0.62,
        peak_time_base=48.0,
        fat_delay_per_g=0.55,
        protein_rise_per_g=0.18,
        fiber_damping=0.022,
        clearance_shape=1.9,
        cgm_noise_sd=2.4,
        dawn_amplitude=6.0,
        resting_hr=62.0,
        meals_per_day=4,
        has_heart_rate=True,
        medication_class=None,
    ),
    SyntheticTraits(
        participant_id="synthetic_p002",
        display_name="Participant 002",
        fasting_glucose=108.0,
        # A markedly steeper carbohydrate responder -- the clearest personalization case.
        carb_sensitivity=1.08,
        peak_time_base=62.0,
        fat_delay_per_g=0.80,
        protein_rise_per_g=0.26,
        fiber_damping=0.013,
        clearance_shape=1.4,
        cgm_noise_sd=3.1,
        dawn_amplitude=9.0,
        resting_hr=71.0,
        meals_per_day=4,
        has_heart_rate=True,
        medication_class=None,
    ),
    SyntheticTraits(
        participant_id="synthetic_p003",
        display_name="Participant 003",
        fasting_glucose=92.0,
        # A flat responder: large meals barely move this participant.
        carb_sensitivity=0.34,
        peak_time_base=38.0,
        fat_delay_per_g=0.35,
        protein_rise_per_g=0.10,
        fiber_damping=0.030,
        clearance_shape=2.4,
        cgm_noise_sd=2.0,
        dawn_amplitude=4.5,
        resting_hr=58.0,
        meals_per_day=3,
        has_heart_rate=True,
        medication_class="biguanide",
    ),
    SyntheticTraits(
        participant_id="synthetic_p004",
        display_name="Participant 004",
        fasting_glucose=118.0,
        carb_sensitivity=0.86,
        # A late peaker -- stresses time-to-peak error.
        peak_time_base=78.0,
        fat_delay_per_g=0.95,
        protein_rise_per_g=0.22,
        fiber_damping=0.018,
        clearance_shape=1.3,
        cgm_noise_sd=3.6,
        dawn_amplitude=11.0,
        resting_hr=76.0,
        meals_per_day=4,
        has_heart_rate=True,
        medication_class="sglt2_inhibitor",
    ),
    SyntheticTraits(
        participant_id="synthetic_p005",
        display_name="Participant 005",
        fasting_glucose=101.0,
        carb_sensitivity=0.71,
        peak_time_base=55.0,
        fat_delay_per_g=0.60,
        protein_rise_per_g=0.20,
        fiber_damping=0.020,
        clearance_shape=1.7,
        cgm_noise_sd=2.7,
        dawn_amplitude=7.0,
        resting_hr=66.0,
        meals_per_day=4,
        # No wearable at all: exercises the missing-modality path end to end.
        has_heart_rate=False,
        medication_class=None,
    ),
    SyntheticTraits(
        participant_id="synthetic_p006",
        display_name="Participant 006",
        fasting_glucose=99.0,
        carb_sensitivity=0.78,
        peak_time_base=52.0,
        fat_delay_per_g=0.58,
        protein_rise_per_g=0.19,
        fiber_damping=0.021,
        clearance_shape=1.8,
        cgm_noise_sd=2.5,
        dawn_amplitude=6.5,
        resting_hr=64.0,
        # Sparse logger: one meal, logged on only three of the seven days. That is
        # below the configured minimum of 4 training episodes, so this participant
        # must come back as `generalized_fallback` -- the honest-degradation path,
        # exercised end to end including in the live demo.
        meals_per_day=1,
        has_heart_rate=True,
        medication_class=None,
        meal_days=(0, 2, 4),
    ),
)

#: Meal archetypes the generator samples from, loosely spanning a realistic macro space.
MEAL_ARCHETYPES: tuple[dict[str, float], ...] = (
    {"carbs_g": 62.0, "protein_g": 12.0, "fat_g": 9.0, "fiber_g": 4.0},     # cereal + milk
    {"carbs_g": 38.0, "protein_g": 26.0, "fat_g": 22.0, "fiber_g": 6.0},    # eggs + toast
    {"carbs_g": 85.0, "protein_g": 24.0, "fat_g": 18.0, "fiber_g": 7.0},    # pasta
    {"carbs_g": 22.0, "protein_g": 34.0, "fat_g": 28.0, "fiber_g": 9.0},    # salad + chicken
    {"carbs_g": 95.0, "protein_g": 16.0, "fat_g": 30.0, "fiber_g": 5.0},    # pizza
    {"carbs_g": 48.0, "protein_g": 30.0, "fat_g": 14.0, "fiber_g": 11.0},   # rice + beans
    {"carbs_g": 15.0, "protein_g": 8.0, "fat_g": 12.0, "fiber_g": 3.0},     # snack
    {"carbs_g": 70.0, "protein_g": 20.0, "fat_g": 25.0, "fiber_g": 8.0},    # burrito
)

#: Nominal meal times by index within a day (hour of day, local == UTC in this fixture).
MEAL_HOURS = (8.0, 12.5, 18.5, 21.0)


def _kcal(carbs: float, protein: float, fat: float) -> float:
    """Atwater approximation, with a little reporting noise added by the caller."""
    return 4.0 * carbs + 4.0 * protein + 9.0 * fat


def meal_response(
    minutes_since_meal: np.ndarray,
    amplitude: float,
    peak_time: float,
    clearance_shape: float,
) -> np.ndarray:
    """A smooth unimodal excursion peaking at `peak_time` with height `amplitude`.

    Shape: ((t/tp) * exp(1 - t/tp)) ** s, a scaled gamma-like surge that is exactly 0
    at t=0, exactly `amplitude` at t=`peak_time`, and decays thereafter. `s` > 1
    sharpens the post-peak decay.

    Chosen for smoothness and controllability, NOT as a physiological claim.
    """
    t = np.clip(minutes_since_meal, 0.0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        unit = (t / peak_time) * np.exp(1.0 - t / peak_time)
    unit = np.nan_to_num(unit, nan=0.0, posinf=0.0, neginf=0.0)
    return amplitude * np.power(np.clip(unit, 0.0, None), clearance_shape)


def _generate_meals(
    traits: SyntheticTraits, days: int, rng: np.random.Generator
) -> list[MealRecord]:
    meals: list[MealRecord] = []
    counter = 0
    logged_days = traits.meal_days if traits.meal_days is not None else tuple(range(days))
    for day in range(days):
        if day not in logged_days:
            continue
        for slot in range(traits.meals_per_day):
            # Jitter the nominal meal time by up to +/- 40 minutes.
            hour = MEAL_HOURS[slot % len(MEAL_HOURS)]
            jitter = float(rng.uniform(-40.0, 40.0))
            timestamp = EPOCH + dt.timedelta(days=day, hours=hour, minutes=jitter)

            archetype = MEAL_ARCHETYPES[int(rng.integers(len(MEAL_ARCHETYPES)))]
            # Portion scaling plus independent per-macro noise, so macros are not
            # perfectly collinear (which would make attribution meaningless).
            scale = float(rng.uniform(0.75, 1.3))
            carbs = round(max(0.0, archetype["carbs_g"] * scale * rng.uniform(0.9, 1.1)), 1)
            protein = round(max(0.0, archetype["protein_g"] * scale * rng.uniform(0.85, 1.15)), 1)
            fat = round(max(0.0, archetype["fat_g"] * scale * rng.uniform(0.85, 1.15)), 1)
            fiber = round(max(0.0, archetype["fiber_g"] * scale * rng.uniform(0.8, 1.2)), 1)
            # Reported calories are noisy relative to Atwater, as logged data is.
            calories = round(_kcal(carbs, protein, fat) * float(rng.uniform(0.94, 1.06)), 0)

            counter += 1
            meals.append(
                MealRecord(
                    meal_id=f"{traits.participant_id}_m{counter:03d}",
                    participant_id=traits.participant_id,
                    timestamp=timestamp,
                    carbs_g=carbs,
                    protein_g=protein,
                    fat_g=fat,
                    fiber_g=fiber,
                    calories_kcal=calories,
                    image_ref=None,
                )
            )
    meals.sort(key=lambda meal: meal.timestamp)
    return meals


def _generate_cgm(
    traits: SyntheticTraits,
    meals: list[MealRecord],
    days: int,
    rng: np.random.Generator,
) -> list[CgmPoint]:
    n_steps = int(days * 24 * 60 / CGM_CADENCE_MINUTES)
    offsets = np.arange(n_steps) * CGM_CADENCE_MINUTES  # minutes since EPOCH

    # Circadian baseline with a dawn rise.
    hours = (offsets / 60.0) % 24.0
    baseline = traits.fasting_glucose + traits.dawn_amplitude * np.sin(
        2.0 * np.pi * (hours - 4.0) / 24.0
    )

    # Superpose each meal's excursion.
    excursion = np.zeros(n_steps)
    for meal in meals:
        minutes_from_epoch = (meal.timestamp - EPOCH).total_seconds() / 60.0
        net_carbs = max(0.0, (meal.carbs_g or 0.0) - 0.5 * (meal.fiber_g or 0.0))
        amplitude = (
            traits.carb_sensitivity * net_carbs
            + traits.protein_rise_per_g * (meal.protein_g or 0.0)
        )
        amplitude *= max(0.25, 1.0 - traits.fiber_damping * (meal.fiber_g or 0.0))
        # Per-meal variability: the same meal does not produce the same curve twice.
        amplitude *= float(rng.uniform(0.82, 1.18))
        peak_time = traits.peak_time_base + traits.fat_delay_per_g * (meal.fat_g or 0.0)
        peak_time *= float(rng.uniform(0.9, 1.1))
        excursion += meal_response(
            offsets - minutes_from_epoch, amplitude, max(15.0, peak_time), traits.clearance_shape
        )

    # AR(1) sensor/physiological noise.
    rho = 0.72
    innovations = rng.normal(0.0, traits.cgm_noise_sd, size=n_steps)
    noise = np.zeros(n_steps)
    for i in range(1, n_steps):
        noise[i] = rho * noise[i - 1] + innovations[i]

    values = baseline + excursion + noise

    # Missingness: scattered dropout plus one contiguous sensor gap, both of which the
    # feature code must tolerate.
    keep = rng.random(n_steps) > 0.03
    gap_start = int(rng.integers(int(n_steps * 0.3), int(n_steps * 0.7)))
    gap_len = int(90 / CGM_CADENCE_MINUTES)  # a 90-minute outage
    keep[gap_start : gap_start + gap_len] = False

    return [
        CgmPoint(
            timestamp=EPOCH + dt.timedelta(minutes=float(offsets[i])),
            glucose_mg_dl=round(float(max(40.0, values[i])), 1),
        )
        for i in range(n_steps)
        if keep[i]
    ]


def _generate_heart_rate(
    traits: SyntheticTraits,
    meals: list[MealRecord],
    days: int,
    rng: np.random.Generator,
) -> list[HeartRatePoint]:
    if not traits.has_heart_rate:
        return []

    n_steps = int(days * 24 * 60 / HR_CADENCE_MINUTES)
    offsets = np.arange(n_steps) * HR_CADENCE_MINUTES
    hours = (offsets / 60.0) % 24.0

    # Lower overnight, higher mid-afternoon.
    values = traits.resting_hr + 9.0 * np.sin(2.0 * np.pi * (hours - 9.0) / 24.0)

    # Modest postprandial elevation.
    for meal in meals:
        minutes_from_epoch = (meal.timestamp - EPOCH).total_seconds() / 60.0
        values += meal_response(offsets - minutes_from_epoch, 6.0, 35.0, 2.0)

    # A couple of exercise bursts per day.
    for day in range(days):
        for _ in range(2):
            start = day * 24 * 60 + float(rng.uniform(7 * 60, 20 * 60))
            values += meal_response(offsets - start, float(rng.uniform(25, 50)), 20.0, 2.5)

    values += rng.normal(0.0, 2.2, size=n_steps)
    keep = rng.random(n_steps) > 0.05

    return [
        HeartRatePoint(
            timestamp=EPOCH + dt.timedelta(minutes=float(offsets[i])),
            heart_rate_bpm=round(float(np.clip(values[i], 38.0, 190.0)), 1),
            resting_heart_rate_bpm=round(traits.resting_hr, 1),
        )
        for i in range(n_steps)
        if keep[i]
    ]


def _generate_medications(
    traits: SyntheticTraits, days: int, rng: np.random.Generator
) -> list[MedicationEvent]:
    if traits.medication_class is None:
        return []
    events: list[MedicationEvent] = []
    for day in range(days):
        for hour in (7.5, 19.5):
            events.append(
                MedicationEvent(
                    participant_id=traits.participant_id,
                    timestamp=EPOCH
                    + dt.timedelta(days=day, hours=hour, minutes=float(rng.uniform(-20, 20))),
                    medication_class=traits.medication_class,
                    dose=500.0 if traits.medication_class == "biguanide" else 10.0,
                    dose_unit="mg",
                )
            )
    events.sort(key=lambda event: event.timestamp)
    return events


def _generate_activity(
    traits: SyntheticTraits, days: int, rng: np.random.Generator
) -> list[ActivityEvent]:
    # Coarse daily step totals only. Activity is optional and not required for the MVP.
    return [
        ActivityEvent(
            participant_id=traits.participant_id,
            timestamp=EPOCH + dt.timedelta(days=day, hours=23, minutes=55),
            activity_value=round(float(rng.uniform(2500, 14000)), 0),
            activity_unit="steps_per_day",
        )
        for day in range(days)
    ]


def _stable_offset(participant_id: str) -> int:
    """Process-stable integer derived from the participant id.

    Deliberately NOT Python's builtin `hash()`: string hashing is salted per process
    (PYTHONHASHSEED), so seeding from it would make the fixture -- and therefore every
    trained artifact and every reported metric -- differ between runs. A SHA-256 digest
    is stable across processes, machines and Python versions.
    """
    digest = hashlib.sha256(participant_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100_000


def build_history(traits: SyntheticTraits, days: int, seed: int) -> ParticipantHistory:
    """Generate one participant's complete synthetic history, deterministically."""
    # Seed per participant so adding a participant does not perturb the others.
    rng = np.random.default_rng(seed + _stable_offset(traits.participant_id))
    meals = _generate_meals(traits, days, rng)
    return ParticipantHistory(
        participant_id=traits.participant_id,
        data_source=DATA_SOURCE,
        cgm=_generate_cgm(traits, meals, days, rng),
        meals=meals,
        heart_rate=_generate_heart_rate(traits, meals, days, rng),
        medications=_generate_medications(traits, days, rng),
        activity=_generate_activity(traits, days, rng),
    )


class SyntheticDevRepository:
    """In-memory `DataRepository` implementation over the synthetic dev fixture.

    Satisfies the same Protocol as Het's canonical repository (see
    `backend/app/services/data_access.py`), so the backend and the ML CLIs can run
    before the real data layer exists. Deterministic: two instances with the same
    seed and `days` return identical records.
    """

    def __init__(self, days: int = 7, seed: int = 20261011) -> None:
        self.days = days
        self.seed = seed
        self.data_source = DATA_SOURCE
        self._histories: dict[str, ParticipantHistory] = {
            traits.participant_id: build_history(traits, days, seed) for traits in TRAITS
        }

    # -- DataRepository interface ------------------------------------------- #

    def list_participants(self) -> list[ParticipantSummary]:
        summaries = []
        for traits in TRAITS:
            history = self._histories[traits.participant_id]
            if history.cgm:
                span = (history.cgm[-1].timestamp - history.cgm[0].timestamp).total_seconds()
                days_available = round(span / 86400.0, 2)
            else:
                days_available = 0.0
            summaries.append(
                ParticipantSummary(
                    participant_id=traits.participant_id,
                    display_name=traits.display_name,
                    data_source=DATA_SOURCE,
                    days_available=days_available,
                    timezone="UTC",
                )
            )
        return sorted(summaries, key=lambda summary: summary.participant_id)

    def get_participant(self, participant_id: str) -> ParticipantSummary | None:
        for summary in self.list_participants():
            if summary.participant_id == participant_id:
                return summary
        return None

    def get_history(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> ParticipantHistory:
        history = self._histories.get(participant_id)
        if history is None:
            return ParticipantHistory(participant_id=participant_id, data_source=DATA_SOURCE)
        if start is None and end is None:
            return history

        def in_range(timestamp: dt.datetime) -> bool:
            if start is not None and timestamp < start:
                return False
            if end is not None and timestamp > end:
                return False
            return True

        return ParticipantHistory(
            participant_id=participant_id,
            data_source=DATA_SOURCE,
            cgm=[point for point in history.cgm if in_range(point.timestamp)],
            meals=[meal for meal in history.meals if in_range(meal.timestamp)],
            heart_rate=[point for point in history.heart_rate if in_range(point.timestamp)],
            medications=[
                event for event in history.medications if in_range(event.timestamp)
            ],
            activity=[event for event in history.activity if in_range(event.timestamp)],
        )

    def get_meals(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> list[MealRecord]:
        return self.get_history(participant_id, start, end).meals

    def export_model_examples(self, cutoff: dt.datetime | None = None) -> list[MealRecord]:
        meals: list[MealRecord] = []
        for participant_id in self._histories:
            for meal in self._histories[participant_id].meals:
                if cutoff is None or meal.timestamp <= cutoff:
                    meals.append(meal)
        meals.sort(key=lambda meal: (meal.timestamp, meal.meal_id))
        return meals
