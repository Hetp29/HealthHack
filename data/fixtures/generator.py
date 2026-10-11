"""Deterministic synthetic development fixtures (data_source = "synthetic_dev").

    python -m data.fixtures.generator                 # writes the committed fixture set
    python -m data.fixtures.generator --out /tmp/x --truth-out /tmp/x_truth --seed 7

SYNTHETIC DEVELOPMENT DATA. These are fictional participants produced by a simple,
hand-written simulator. They exist only to unblock development until the organizer
dataset arrives. Nothing here is physiology learned from people, and no result computed
on it is clinical evidence of anything.

Outputs
-------
``<out>/``        canonical CSVs (participants, cgm, heart_rate, meals, medications,
                  activity) + ``manifest.json``. These are the ONLY files a model may read.
``<truth-out>/``  ``simulator_truth.json``: the hidden generating parameters and true
                  meal times/macros. NEVER a model input; it exists so tests can check
                  that the architecture separates individuals. Kept in a separate
                  directory on purpose.

Simulator (per participant, on that participant's CGM grid)
-----------------------------------------------------------
glucose(t) = baseline + dawn_bump(t) + sum_meals carb_response + protein_response
             - activity_effect(t) + AR(1) drift + sensor noise, rounded, clipped to 40..400
carb response:   A * (tau/tp) * exp(1 - tau/tp)      (peaks at tp, A in mg/dL)
  A  = carb_sensitivity * max(carbs - 0.5*fiber, 0) * circadian(meal hour)
       / (1 + 0.008*fat + 0.004*protein)
  tp = time_to_peak * (1 + 0.005*fat)
protein response: small, slower (tp * 1.6), 0.15 mg/dL per g
activity effect:  activity_sensitivity * EMA(steps per 5 min, 30 min) / 100
heart rate:       resting + steps-driven rise + small post-meal rise - sleep dip + noise
Medications are recorded events only: NO simulated pharmacological effect (the contract
forbids assuming a medication name maps to a clinical effect).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from data.timeutil import to_rfc3339

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20261011
DEFAULT_START = date(2026, 10, 1)  # day 1; day 8 (2026-10-08) is left for prediction demos
DEFAULT_DAYS = 7
CGM_STEP = timedelta(minutes=5)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data" / "fixtures" / "synthetic_dev"
DEFAULT_TRUTH_OUT = REPO_ROOT / "data" / "fixtures" / "simulator_truth"

SYNTHETIC_LABEL = (
    "SYNTHETIC DEVELOPMENT DATA (synthetic_dev). Fictional participants from a hand-written "
    "simulator; not real people, not learned physiology, not clinical evidence."
)


@dataclass(frozen=True)
class SimParams:
    participant_id: str
    display_name: str
    timezone: str
    baseline_mg_dl: float
    carb_sensitivity: float  # mg/dL peak rise per gram of effective carbohydrate
    time_to_peak_min: float
    circadian_amp: float  # + makes evening meals produce larger responses
    dawn_rise_mg_dl: float
    activity_sensitivity: float
    resting_hr: float
    hr_activity_gain: float  # bpm per 100 steps in a 5-min bin
    drift_sd: float
    sensor_noise_sd: float
    cgm_offset_min: int  # sensor clock offset from :00/:05
    has_heart_rate: bool = True
    has_activity: bool = True
    snack_rate: float = 0.5
    overlap_rate: float = 0.2  # chance per day of a snack 45-90 min after lunch
    walks_per_day: tuple[int, int] = (1, 2)
    post_meal_walk_rate: float = 0.25
    medications: tuple = ()  # (class, dose, unit, "HH:MM", skip_prob)
    carbs_only_snack_logging: bool = False
    gap_blocks: tuple = ()  # (day_index, "HH:MM", minutes) deterministic signal-loss gaps
    logged_meal_limit: int | None = None  # sparse logger: eats normally, logs only N meals


# Six phenotypes chosen to be clearly distinct so personalization has something to find.
PARTICIPANTS: tuple[SimParams, ...] = (
    SimParams("synthetic_p001", "Participant 001", "America/New_York", 92, 0.85, 45, 0.10, 6, 2.0, 62, 6.0, 1.0, 2.5, 0,
              gap_blocks=((2, "03:10", 95),)),
    SimParams("synthetic_p002", "Participant 002", "America/Chicago", 104, 1.25, 60, 0.20, 10, 1.5, 70, 5.0, 1.2, 3.0, 2,
              medications=(("biguanide", 500.0, "mg", "08:00", 0.05), ("biguanide", 500.0, "mg", "19:30", 0.1)),
              gap_blocks=((4, "14:20", 50),)),
    SimParams("synthetic_p003", "Participant 003", "America/Los_Angeles", 97, 0.60, 35, 0.00, 4, 3.0, 56, 5.5, 0.9, 2.0, 4,
              walks_per_day=(2, 3), post_meal_walk_rate=0.5, gap_blocks=((5, "09:40", 180),)),
    SimParams("synthetic_p004", "Participant 004", "America/New_York", 121, 1.55, 75, 0.30, 14, 1.0, 76, 4.0, 1.5, 3.5, 1,
              walks_per_day=(0, 1), post_meal_walk_rate=0.1,
              medications=(("basal_insulin", 14.0, "units", "22:00", 0.0),),
              gap_blocks=((1, "11:00", 300),)),  # simulated sensor change
    # No heart-rate (or activity) data at all -- the missing-modality case.
    SimParams("synthetic_p005", "Participant 005", "America/Denver", 88, 1.00, 50, 0.35, 5, 0.0, 66, 0.0, 1.0, 2.5, 3,
              snack_rate=0.8, overlap_rate=0.35, carbs_only_snack_logging=True,
              has_heart_rate=False, has_activity=False, walks_per_day=(0, 0), post_meal_walk_rate=0.0,
              gap_blocks=((3, "20:15", 40),)),
    # Sparse logger: eats ~3-4 meals a day (all affect glucose) but logs only 3 in a week.
    SimParams("synthetic_p006", "Participant 006", "America/Phoenix", 110, 1.10, 55, 0.15, 8, 1.5, 72, 5.0, 1.3, 3.0, 0,
              logged_meal_limit=3,
              gap_blocks=((6, "01:00", 70),)),
)

# (name, carbs, protein, fat, fiber) per typical serving, grams.
MEAL_LIBRARY = {
    "breakfast": [("oatmeal_berries", 54, 9, 6, 8), ("eggs_toast", 30, 20, 16, 4), ("yogurt_granola", 48, 15, 9, 4),
                  ("bagel_cream_cheese", 58, 12, 12, 2), ("smoothie", 62, 8, 4, 6)],
    "lunch": [("chicken_salad", 18, 34, 18, 7), ("turkey_sandwich", 46, 28, 14, 5), ("rice_bowl", 78, 24, 12, 4),
              ("pasta_tomato", 82, 16, 10, 6), ("burrito", 70, 26, 22, 9)],
    "dinner": [("salmon_rice_veg", 52, 34, 20, 6), ("pizza_slices", 74, 26, 28, 4), ("stir_fry_noodles", 80, 22, 16, 5),
               ("steak_potato", 40, 42, 26, 5), ("lentil_curry_rice", 76, 22, 12, 14)],
    "snack": [("apple", 25, 0, 0, 4), ("cookie", 22, 2, 8, 1), ("granola_bar", 28, 4, 7, 3), ("crackers_cheese", 18, 7, 9, 1),
              ("banana", 27, 1, 0, 3), ("chips", 16, 2, 10, 1)],
}
MEAL_WINDOWS = {"breakfast": ("07:00", 90), "lunch": ("12:00", 90), "dinner": ("18:00", 120), "snack": ("15:00", 100)}


@dataclass
class _Meal:
    meal_id: str
    kind: str
    name: str
    true_time: datetime
    logged_time: datetime
    true_macros: dict
    logged: dict = field(default_factory=dict)


def _hm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def _local(day: date, hhmm: str, tz: ZoneInfo, plus_min: float = 0.0) -> datetime:
    return (datetime.combine(day, _hm(hhmm), tzinfo=tz) + timedelta(minutes=plus_min)).astimezone(timezone.utc)


def _r1(x: float) -> float:
    return round(x, 1)


def _simulate(p: SimParams, seed: int, start: date, days: int):
    rng = random.Random(f"glucotwin:{seed}:{p.participant_id}")
    tz = ZoneInfo(p.timezone)
    day_list = [start + timedelta(days=i) for i in range(days)]
    t0 = datetime.combine(start, time(0, 0), tzinfo=tz).astimezone(timezone.utc) + timedelta(minutes=p.cgm_offset_min)
    t_end = datetime.combine(start + timedelta(days=days), time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    grid = []
    t = t0
    while t < t_end:
        grid.append(t)
        t += CGM_STEP

    # ---- meals
    meals: list[_Meal] = []
    for di, day in enumerate(day_list):
        plan = ["breakfast", "lunch", "dinner"]
        if rng.random() < p.snack_rate:
            plan.append("snack")
        overlap = rng.random() < p.overlap_rate
        lunch_time = None
        for kind in plan:
            start_hm, width = MEAL_WINDOWS[kind]
            true_time = _local(day, start_hm, tz, rng.uniform(0, width))
            if kind == "lunch":
                lunch_time = true_time
            name, c, pr, f, fi = rng.choice(MEAL_LIBRARY[kind])
            scale = rng.uniform(0.75, 1.3)
            meals.append(_Meal("", kind, name, true_time, true_time,
                               {"carbs_g": c * scale, "protein_g": pr * scale, "fat_g": f * scale, "fiber_g": fi * scale}))
        if overlap and lunch_time is not None:
            name, c, pr, f, fi = rng.choice(MEAL_LIBRARY["snack"])
            meals.append(_Meal("", "snack", name, lunch_time + timedelta(minutes=rng.uniform(45, 90)), lunch_time,
                               {"carbs_g": c, "protein_g": pr, "fat_g": f, "fiber_g": fi}))
    meals.sort(key=lambda m: m.true_time)
    no_nutrition_index = rng.randrange(len(meals)) if p.participant_id == "synthetic_p003" else -1
    logged_idx = set(range(len(meals)))
    if p.logged_meal_limit is not None:
        logged_idx = set(rng.sample(range(len(meals)), p.logged_meal_limit))
    n_logged = 0
    for n, meal in enumerate(meals, start=1):
        if n - 1 in logged_idx:
            n_logged += 1
            meal.meal_id = f"{p.participant_id}_m{n_logged:03d}"
        # Logged time: people log a few minutes off; rounded to the minute.
        jitter = rng.choice([0, 0, 0, 1, 2, 3, 5, -2, 8])
        meal.logged_time = (meal.true_time + timedelta(minutes=jitter)).replace(second=0, microsecond=0)
        tm = meal.true_macros
        noisy = {k: max(v * (1 + rng.gauss(0, 0.08)), 0.0) for k, v in tm.items()}  # self-report error
        kcal = 4 * noisy["carbs_g"] + 4 * noisy["protein_g"] + 9 * noisy["fat_g"]
        logged = {k: _r1(v) for k, v in noisy.items()}
        logged["calories_kcal"] = float(round(kcal * (1 + rng.gauss(0, 0.04))))
        if rng.random() < 0.12:
            logged["fiber_g"] = None
        if rng.random() < 0.08:
            logged["calories_kcal"] = None
        if p.carbs_only_snack_logging and meal.kind == "snack":
            logged = {"carbs_g": logged["carbs_g"], "protein_g": None, "fat_g": None, "fiber_g": None, "calories_kcal": None}
        if n - 1 == no_nutrition_index:
            logged = {"carbs_g": None, "protein_g": None, "fat_g": None, "fiber_g": None, "calories_kcal": None}
        meal.logged = logged

    # ---- activity: steps per 5-min bin
    steps = [0.0] * len(grid)
    walk_starts: list[datetime] = []
    for day in day_list:
        lo, hi = p.walks_per_day
        for _ in range(rng.randint(lo, hi) if hi else 0):
            walk_starts.append(_local(day, "09:00", tz, rng.uniform(0, 12 * 60)))
    for meal in meals:
        if rng.random() < p.post_meal_walk_rate:
            walk_starts.append(meal.true_time + timedelta(minutes=rng.uniform(10, 30)))
    walks = [(ws, ws + timedelta(minutes=rng.uniform(20, 50))) for ws in walk_starts]
    for i, ts in enumerate(grid):
        hour = ts.astimezone(tz).hour + ts.astimezone(tz).minute / 60
        asleep = hour >= 23 or hour < 6.5
        base = rng.uniform(0, 8) if asleep else max(rng.gauss(55, 30), 0)
        walking = any(a <= ts < b for a, b in walks)
        steps[i] = base + (rng.gauss(520, 60) if walking else 0.0)

    # ---- glucose
    ema = 0.0
    alpha = 1 - math.exp(-5 / 30)
    drift = 0.0
    glucose: list[float | None] = []
    for i, ts in enumerate(grid):
        local = ts.astimezone(tz)
        hour = local.hour + local.minute / 60
        dawn = p.dawn_rise_mg_dl * math.exp(-((hour - 6.0) ** 2) / (2 * 1.2**2))
        meal_sum = 0.0
        for meal in meals:
            tau = (ts - meal.true_time).total_seconds() / 60
            if tau <= 0 or tau > 480:
                continue
            m = meal.true_macros
            mh = meal.true_time.astimezone(tz)
            mhour = mh.hour + mh.minute / 60
            circ = 1 + p.circadian_amp * max(min((mhour - 13) / 7, 1), -1)
            eff = max(m["carbs_g"] - 0.5 * m["fiber_g"], 0.0)
            amp = p.carb_sensitivity * eff * circ / (1 + 0.008 * m["fat_g"] + 0.004 * m["protein_g"])
            tp = p.time_to_peak_min * (1 + 0.005 * m["fat_g"])
            meal_sum += amp * (tau / tp) * math.exp(1 - tau / tp)
            tpp = tp * 1.6
            meal_sum += 0.15 * m["protein_g"] * (tau / tpp) * math.exp(1 - tau / tpp)
        ema += alpha * (steps[i] - ema)
        drift = 0.95 * drift + rng.gauss(0, p.drift_sd)
        value = p.baseline_mg_dl + dawn + meal_sum - p.activity_sensitivity * ema / 100 + drift
        value += rng.gauss(0, p.sensor_noise_sd)
        glucose.append(float(min(max(round(value), 40), 400)))

    # ---- CGM missingness: random dropouts + deterministic signal-loss blocks
    for day_index, hhmm, minutes in p.gap_blocks:
        g_start = _local(day_list[day_index], hhmm, tz)
        g_end = g_start + timedelta(minutes=minutes)
        for i, ts in enumerate(grid):
            if g_start <= ts < g_end:
                glucose[i] = None
    for i in range(len(glucose)):
        if rng.random() < 0.015:
            glucose[i] = None

    # ---- heart rate (own clock aligned to :00/:05), daily resting value
    hr_rows = []
    if p.has_heart_rate:
        daily_rest = {d: _r1(p.resting_hr + rng.gauss(0, 1.5)) for d in day_list}
        hr_t = datetime.combine(start, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
        idx = 0
        while hr_t < t_end:
            while idx + 1 < len(grid) and grid[idx + 1] <= hr_t:
                idx += 1
            local = hr_t.astimezone(tz)
            hour = local.hour + local.minute / 60
            asleep = hour >= 23 or hour < 6.5
            post_meal = any(0 < (hr_t - m.true_time).total_seconds() / 60 <= 90 for m in meals)
            bpm = p.resting_hr + p.hr_activity_gain * steps[idx] / 100 + (3 if post_meal else 0) - (6 if asleep else 0)
            bpm += rng.gauss(0, 2.5)
            if rng.random() >= 0.04:
                hr_rows.append((hr_t, float(round(bpm)), daily_rest.get(local.date())))
            hr_t += CGM_STEP

    # ---- activity rows: steps summed per 15-min window
    act_rows = []
    if p.has_activity:
        for i in range(0, len(grid), 3):
            act_rows.append((grid[i] - timedelta(minutes=p.cgm_offset_min), float(round(sum(steps[i : i + 3])))))

    # ---- medications (events only)
    med_rows = []
    for cls, dose, unit, hhmm, skip in p.medications:
        for day in day_list:
            if rng.random() < skip:
                continue
            ts = _local(day, hhmm, tz, rng.uniform(-20, 20)).replace(second=0, microsecond=0)
            med_rows.append((ts, cls, dose, unit))
    med_rows.sort()

    cgm_rows = [(ts, g) for ts, g in zip(grid, glucose) if g is not None]
    return cgm_rows, hr_rows, meals, med_rows, act_rows


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return to_rfc3339(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([_fmt(v) for v in row])


def generate(
    out_dir: str | Path = DEFAULT_OUT,
    truth_dir: str | Path = DEFAULT_TRUTH_OUT,
    *,
    seed: int = DEFAULT_SEED,
    start: date = DEFAULT_START,
    days: int = DEFAULT_DAYS,
    participants: tuple[SimParams, ...] = PARTICIPANTS,
) -> dict:
    """Write the fixture set and return its manifest. Same arguments -> identical bytes."""
    out_dir, truth_dir = Path(out_dir), Path(truth_dir)
    if out_dir.resolve() == truth_dir.resolve():
        raise ValueError("simulator truth must not share a directory with model-readable fixtures")
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    rows = {k: [] for k in ("participants", "cgm", "heart_rate", "meals", "medications", "activity")}
    truth = {"label": SYNTHETIC_LABEL, "warning": "NOT A MODEL INPUT. Hidden simulator variables for tests only.",
             "generator_version": GENERATOR_VERSION, "seed": seed, "participants": {}}
    for p in participants:
        cgm_rows, hr_rows, meals, med_rows, act_rows = _simulate(p, seed, start, days)
        pid = p.participant_id
        rows["participants"].append([pid, p.display_name, p.timezone])
        rows["cgm"] += [[pid, ts, g] for ts, g in cgm_rows]
        rows["heart_rate"] += [[pid, ts, bpm, rest] for ts, bpm, rest in hr_rows]
        rows["meals"] += [
            [m.meal_id, pid, m.logged_time, m.logged["carbs_g"], m.logged["protein_g"], m.logged["fat_g"],
             m.logged["fiber_g"], m.logged["calories_kcal"], None]
            for m in meals
            if m.meal_id
        ]
        rows["medications"] += [
            [f"{pid}_med{n:03d}", pid, ts, cls, dose, unit] for n, (ts, cls, dose, unit) in enumerate(med_rows, start=1)
        ]
        rows["activity"] += [[pid, ts, steps, "steps"] for ts, steps in act_rows]
        truth["participants"][pid] = {
            "params": asdict(p),
            "meals": [
                {"meal_id": m.meal_id or None, "logged": bool(m.meal_id), "kind": m.kind, "template": m.name, "true_time": to_rfc3339(m.true_time),
                 "true_macros": {k: round(v, 2) for k, v in m.true_macros.items()}}
                for m in meals
            ],
        }

    headers = {
        "participants": ["participant_id", "display_name", "timezone"],
        "cgm": ["participant_id", "timestamp", "glucose_mg_dl"],
        "heart_rate": ["participant_id", "timestamp", "heart_rate_bpm", "resting_heart_rate_bpm"],
        "meals": ["meal_id", "participant_id", "timestamp", "carbs_g", "protein_g", "fat_g", "fiber_g",
                  "calories_kcal", "image_ref"],
        "medications": ["id", "participant_id", "timestamp", "medication_class", "dose", "dose_unit"],
        "activity": ["participant_id", "timestamp", "activity_value", "activity_unit"],
    }
    files = {}
    for name, header in headers.items():
        path = out_dir / f"{name}.csv"
        _write_csv(path, header, rows[name])
        files[path.name] = {"rows": len(rows[name]), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    manifest = {
        "data_source": "synthetic_dev",
        "label": SYNTHETIC_LABEL,
        "generator": "data/fixtures/generator.py",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "local_start_date": start.isoformat(),
        "days": days,
        "participants": [p.participant_id for p in participants],
        "files": files,
        "simulator_truth": "kept outside this directory on purpose; never a model input",
        "assumptions": [
            "Hand-written simulator; parameters are illustrative, not fitted to any human data.",
            "CGM nominal 5-min cadence on a per-participant clock offset; values rounded and clipped to 40-400 mg/dL "
            "like a consumer CGM display.",
            "~1.5% random CGM dropouts plus one deterministic signal-loss block per participant.",
            "Logged meal times differ from true times by 0-8 min; logged macros carry ~8% self-report noise; some "
            "fiber/calorie labels are missing; synthetic_p005 logs snacks as carbs only; synthetic_p006 logs only 3 "
            "of its meals (unlogged meals still move glucose); one synthetic_p003 meal has "
            "no nutrition logged.",
            "Some days include a snack 45-90 min after lunch, producing overlapping meal responses.",
            "Heart rate: 5-min samples, ~4% missing; synthetic_p005 has no heart-rate or activity data at all.",
            "Activity: steps summed per 15-min window.",
            "Medications are recorded events with NO simulated physiological effect.",
            "No meal images exist; image_ref is null everywhere.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (truth_dir / "simulator_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic_dev fixtures.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--truth-out", default=str(DEFAULT_TRUTH_OUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args(argv)
    manifest = generate(args.out, args.truth_out, seed=args.seed, days=args.days)
    for name, info in manifest["files"].items():
        print(f"{name:<18} {info['rows']:>6} rows  sha256 {info['sha256'][:16]}…")
    print(f"wrote {args.out} (seed {args.seed}); simulator truth -> {args.truth_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
