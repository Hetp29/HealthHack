"""Development experiments: feature ablations and model comparison.

    python -m ml.experiments --db-url sqlite:///data/local/cgmacros_v2.db --stage models

Every number produced here comes from rolling, participant-aware, purged validation on
the DEVELOPMENT POOL only (the Day-8 train + validation folds). The 394 held-out Day-8
episodes are never read: they have already been inspected, so selecting on them would
be selection on the test set.

Episodes are cached to disk because building them from the database takes ~70 s and
every experiment needs the same set.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import pickle
import time

from ml.config import DEFAULT_CONFIG, Config
from ml.devvalidation import (
    format_table,
    paired_participant_bootstrap,
    rolling_folds,
    score_candidate,
)
from ml.evaluate import split_day8_holdout
from ml.targets import build_episodes

CACHE_DIR = pathlib.Path("ml/_devdata")
RESULTS_DIR = pathlib.Path("docs/experiments")

#: Feature groups for ablation, named by what they contribute.
FEATURE_GROUPS: dict[str, list[str]] = {
    "glucose context": [
        "cgm_slope_30min_mg_dl_per_min",
        "cgm_mean_60min_mg_dl",
        "cgm_std_60min_mg_dl",
    ],
    "baseline glucose": ["premeal_glucose_mg_dl"],
    "macros": ["carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"],
    "carbs only": ["carbs_g"],
    "circadian": ["tod_sin", "tod_cos"],
    "prior meal": ["minutes_since_prior_meal", "prior_meal_carbs_g"],
    "heart rate": ["hr_mean_30min_bpm"],
    "medication flag": ["medication_exposure_recent"],
    "data recency": ["minutes_since_last_cgm"],
}


def load_dev_pool(db_url: str, config: Config, refresh: bool = False):
    """The Day-8 train + validation episodes, cached. Test episodes are discarded here."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "dev_pool.pkl"
    if cache.exists() and not refresh:
        with cache.open("rb") as handle:
            payload = pickle.load(handle)
        if payload.get("db_url") == db_url:
            print(f"loaded {len(payload['dev'])} dev-pool episodes from cache")
            return payload["dev"], payload["meta"]

    from ml.train import build_repository

    repository = build_repository("sqlite", db_url=db_url)
    start = time.perf_counter()
    episodes, report = build_episodes(repository, config)
    train, validation, test = split_day8_holdout(episodes, config)
    dev = sorted(train + validation, key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    meta = {
        "db_url": db_url,
        "build_seconds": round(time.perf_counter() - start, 1),
        "exclusions": report.as_dict(),
        "dev_pool": len(dev),
        "reserved_test": len(test),
        "participants": len({e.participant_id for e in dev}),
    }
    with cache.open("wb") as handle:
        pickle.dump({"db_url": db_url, "dev": dev, "meta": meta}, handle)
    print(f"built {len(dev)} dev-pool episodes in {meta['build_seconds']}s "
          f"({len(test)} test episodes reserved and NOT loaded into any experiment)")
    return dev, meta


def run_stage(stage: str, dev, config: Config, n_folds: int) -> dict:
    folds = rolling_folds(dev, config, n_folds=n_folds)
    print(f"\nrolling folds (n_folds={n_folds}, purged, participant-aware):")
    for fold in folds:
        print(f"  {fold}  participants={len({e.participant_id for e in fold.validate})}")

    if stage == "personalization":
        from ml.candidates import personalization_candidates

        candidates = personalization_candidates()
    elif stage == "ablation":
        from ml.candidates import ablation_candidates
        from ml.features import FEATURE_NAMES

        candidates = ablation_candidates(FEATURE_GROUPS, list(FEATURE_NAMES))
    else:
        from ml.candidates import core_candidates

        candidates = core_candidates()

    results = []
    for candidate in candidates:
        start = time.perf_counter()
        result = score_candidate(folds, candidate, config)
        print(
            f"  {candidate.name:24s} MAE {result.mae:7.3f}  RMSE {result.rmse:7.3f}  "
            f"folds {result.fold_mae}  ({time.perf_counter()-start:.0f}s)"
        )
        results.append(result)

    print("\n" + format_table(results))
    return {"stage": stage, "n_folds": n_folds, "results": results, "folds": folds}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument(
        "--stage", default="models", choices=["models", "ablation", "personalization"]
    )
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--out", default=None, help="write a JSON summary here")
    args = parser.parse_args(argv)

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG, refresh=args.refresh_cache)
    print(json.dumps(meta, indent=1))
    outcome = run_stage(args.stage, dev, DEFAULT_CONFIG, args.n_folds)

    results = outcome["results"]
    baseline = next(
        (r for r in results if "ridge_personal" in r.name or "(control)" in r.name), None
    )
    if baseline is not None:
        print(f"\nparticipant-clustered bootstrap vs BASELINE ({baseline.name}):")
        print("  candidate                 ΔMAE    95% CI            excludes 0")
        for result in results:
            if result is baseline:
                continue
            ci = paired_participant_bootstrap(baseline, result)
            if ci["point"] is None:
                print(f"  {result.name:24s} (not comparable: {ci['note']})")
                continue
            print(
                f"  {result.name:24s} {ci['point']:+7.3f}  "
                f"[{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]   {ci['excludes_zero']}"
            )

    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "meta": meta,
                    "stage": outcome["stage"],
                    "n_folds": outcome["n_folds"],
                    "rows": [r.as_row() for r in results],
                    "participant_mae": {r.name: r.participant_mae for r in results},
                    "fold_mae": {r.name: r.fold_mae for r in results},
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    from ml.experiments import main as _main

    raise SystemExit(_main())
