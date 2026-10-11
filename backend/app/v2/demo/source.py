"""The research-data bundle a demonstration replays, and the rules that choose it.

A *bundle* is one participant's genuine CGMacros recording, cut to the replay window:
glucose readings and meal records exactly as the dataset has them. It is research
data, so it is never committed -- it is built locally from the warehouse by
`python -m scripts.demo_select` and delivered like the model artifact. What IS
committed is the *manifest*: which participant, which meals, the counts, the reasons,
and the bundle's SHA-256, so every run can prove it is replaying the selected data.

Nothing here generates, smooths or fills a glucose value.
"""

from __future__ import annotations

import bisect
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib

SCHEMA = "glucotwin-demo-bundle/1"
MANIFEST_PATH = pathlib.Path(__file__).with_name("manifest.json")

WINDOW = dt.timedelta(minutes=120)
#: Response window plus the standard 30-minute sensor-arrival grace.
REVEAL_AFTER = dt.timedelta(minutes=150)
BASELINE_TOLERANCE = dt.timedelta(minutes=15)
HORIZON_TOLERANCE = dt.timedelta(minutes=10)
MIN_CURVE_POINTS = 20
RETRIEVAL_MIN_HISTORY = 5
LOOKBACK = dt.timedelta(days=8)
MACRO_BOUNDS = {"carbs_g": 1000.0, "protein_g": 1000.0, "fat_g": 1000.0,
                "fiber_g": 200.0, "calories_kcal": 10000.0}
MACROS = tuple(MACRO_BOUNDS)

#: How a bundle's data is labelled in the application store. A bundle that is not
#: genuine research data can never be stored as though it were.
STORE_LABELS = {
    "research_dataset": ("research_cgmacros", "research_dataset"),
    "test_fixture": ("demo", "demo"),
}


class BundleError(RuntimeError):
    """The bundle is absent, malformed, or not the one the manifest selected."""


def _parse(text: str) -> dt.datetime:
    value = dt.datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise BundleError(f"bundle timestamp without an offset: {text!r}")
    return value.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class BundleMeal:
    source_meal_id: str
    occurred_at: dt.datetime
    macros: dict[str, float]


@dataclasses.dataclass(frozen=True)
class Bundle:
    scenario_id: str
    data_class: str
    source: dict
    timezone: str
    #: (observed_at, mg/dL), ascending, exactly as the source recorded them.
    cgm: tuple[tuple[dt.datetime, float], ...]
    meals: tuple[BundleMeal, ...]
    device_id: str
    sha256: str

    @property
    def store_labels(self) -> tuple[str, str]:
        return STORE_LABELS[self.data_class]

    @property
    def start(self) -> dt.datetime:
        """Replay start: shortly before the first meal."""
        return self.meals[0].occurred_at - dt.timedelta(minutes=30)

    @property
    def end(self) -> dt.datetime:
        return self.cgm[-1][0]

    def readings_between(self, after: dt.datetime | None, upto: dt.datetime):
        """Readings with `after < observed_at <= upto` -- the release primitive."""
        times = [t for t, _ in self.cgm]
        lo = 0 if after is None else bisect.bisect_right(times, after)
        return self.cgm[lo:bisect.bisect_right(times, upto)]


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_payload(*, scenario_id: str, data_class: str, source: dict, timezone: str,
                 device_id: str, cgm: list[tuple[dt.datetime, float]],
                 meals: list[tuple[str, dt.datetime, dict]]) -> dict:
    if data_class not in STORE_LABELS:
        raise BundleError(f"unknown data_class {data_class!r}")
    return {
        "schema": SCHEMA, "scenario_id": scenario_id, "data_class": data_class,
        "source": source, "timezone": timezone, "device_id": device_id,
        "cgm": [[_iso(t), float(v)] for t, v in sorted(cgm)],
        "meals": [{"source_meal_id": i, "occurred_at": _iso(t),
                   "macros": {k: float(m[k]) for k in MACROS}}
                  for i, t, m in sorted(meals, key=lambda x: x[1])],
    }


def bundle_from_payload(payload: dict) -> Bundle:
    if payload.get("schema") != SCHEMA:
        raise BundleError(f"unsupported bundle schema {payload.get('schema')!r}")
    if not payload.get("meals") or not payload.get("cgm"):
        raise BundleError("bundle has no meals or no glucose readings")
    return Bundle(
        scenario_id=payload["scenario_id"], data_class=payload["data_class"],
        source=payload["source"], timezone=payload["timezone"], device_id=payload["device_id"],
        cgm=tuple((_parse(t), float(v)) for t, v in payload["cgm"]),
        meals=tuple(BundleMeal(m["source_meal_id"], _parse(m["occurred_at"]), dict(m["macros"]))
                    for m in payload["meals"]),
        sha256=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
    )


def load_manifest(path: pathlib.Path = MANIFEST_PATH) -> dict:
    if not path.exists():
        raise BundleError(f"demo manifest not found at {path}; run `python -m scripts.demo_select`")
    return json.loads(path.read_text())


def load_bundle(path: pathlib.Path, *, expected_sha256: str | None) -> Bundle:
    """Load a bundle file, refusing one that is not the selected data.

    There is deliberately no fallback: a missing bundle is an error that says how to
    obtain it, never a cue to synthesize readings.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise BundleError(
            f"demo data bundle not found at {path}. It is research data and is not in "
            "git. Build it with `python -m scripts.demo_select` (needs the local CGMacros "
            "warehouse), or copy it from the team's artifact store, then set "
            "GLUCOTWIN_DEMO_BUNDLE. No substitute data will be generated."
        )
    bundle = bundle_from_payload(json.loads(path.read_text()))
    if expected_sha256 is not None and bundle.sha256 != expected_sha256:
        raise BundleError(
            f"demo bundle checksum mismatch: the manifest selected {expected_sha256}, "
            f"this file is {bundle.sha256}. Rebuild it with `python -m scripts.demo_select`."
        )
    return bundle


# --------------------------------------------------------------------------- #
# model-free eligibility, mirroring the v2 scoring policy (DECISIONS D-009)
# --------------------------------------------------------------------------- #


def assess_meals(cgm_times: list[dt.datetime], meals: list[tuple[str, dt.datetime, dict | None]],
                 all_meal_times: list[dt.datetime]) -> list[dict]:
    """For each meal, what the v2 rules will conclude -- from timestamps alone.

    This reads no glucose VALUE and runs no model, so nothing chosen with it can be
    chosen for how well the model happens to forecast it.
    """
    out = []
    for meal_id, at, macros in meals:
        loggable = macros is not None and all(
            macros.get(k) is not None and 0 <= macros[k] <= bound for k, bound in MACRO_BOUNDS.items())
        lo = bisect.bisect_right(cgm_times, at)
        baseline = lo > 0 and at - cgm_times[lo - 1] <= BASELINE_TOLERANCE
        hi = bisect.bisect_right(cgm_times, at + WINDOW)
        in_window = cgm_times[lo:hi]
        horizons = all(
            any(abs(t - (at + dt.timedelta(minutes=h))) <= HORIZON_TOLERANCE
                for t in cgm_times[max(0, lo - 3):hi + 3])
            for h in (30, 60, 90, 120))
        intervening = any(at < other <= at + WINDOW for other in all_meal_times)
        reveal_data = bool(cgm_times) and cgm_times[-1] >= at + REVEAL_AFTER
        reasons = []
        if not loggable:
            reasons.append("macros_incomplete_or_out_of_contract_bounds")
        if not baseline:
            reasons.append("no_baseline_reading_within_15_min")
        if len(in_window) < MIN_CURVE_POINTS:
            reasons.append("fewer_than_20_readings_in_window")
        if not horizons:
            reasons.append("missing_horizon_reading")
        if intervening:
            reasons.append("intervening_meal")
        out.append({
            "source_meal_id": meal_id, "occurred_at": at, "loggable": loggable,
            "forecastable": loggable and baseline, "readings_in_window": len(in_window),
            "outcome_data_available": reveal_data,
            "expected_eligible": not reasons, "expected_ineligibility_reasons": reasons,
        })
    return out


def expected_admissible(assessed: list[dict], index: int) -> int:
    """Eligible earlier meals whose window ended STRICTLY before meal `index`."""
    at = assessed[index]["occurred_at"]
    return sum(1 for a in assessed[:index]
               if a["expected_eligible"] and a["occurred_at"] + WINDOW < at)


def plan_window(assessed: list[dict]) -> dict | None:
    """Choose the replay window inside one participant's out-of-fit meals.

    The window runs from the first meal to the second forecastable meal after
    retrieval first becomes active, so it shows: no history, a growing history, the
    gate opening at five, and forecasts that then use that history. `None` when this
    participant cannot show that.
    """
    activation = next(
        (i for i, a in enumerate(assessed)
         if a["forecastable"] and expected_admissible(assessed, i) >= RETRIEVAL_MIN_HISTORY), None)
    if activation is None:
        return None
    later = [i for i in range(activation + 1, len(assessed))
             if assessed[i]["forecastable"] and assessed[i]["outcome_data_available"]]
    if len(later) < 2:
        return None
    final = later[1]
    window = assessed[:final + 1]
    if any(not a["loggable"] for a in window):
        return None
    return {
        "activation_index": activation, "final_index": final, "meal_count": final + 1,
        "ineligible_before_activation": sum(1 for a in assessed[:activation] if not a["expected_eligible"]),
        "ineligible_in_window": sum(1 for a in window if not a["expected_eligible"]),
    }


#: The selection rule, fixed before any forecast is looked at. Lower sorts first.
SELECTION_RULE = (
    "Among participants whose meals after their Day-8 fit cutoff can show the whole "
    "progression (plan_window is not None): require at least one ineligible meal before "
    "retrieval activates, so exclusion is demonstrated; then prefer the fewest meals "
    "needed; then the lowest participant id. No forecast, error or glucose value is "
    "consulted."
)


def rank_key(participant_id: str, plan: dict) -> tuple:
    return (0 if plan["ineligible_before_activation"] >= 1 else 1, plan["meal_count"], participant_id)
