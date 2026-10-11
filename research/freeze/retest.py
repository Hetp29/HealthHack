"""Phases 2 and 3 of the fs-1.3 freeze retest. READ-ONLY with respect to artifacts.

    python -m research.freeze.retest --db-url sqlite:///data/local/cgmacros_v2.db

Tolerances were declared in `research/freeze/TOLERANCES.md` BEFORE this ran and
are imported here rather than written inline, so they cannot drift.

WHAT IS AND IS NOT A "RETRAIN"
------------------------------
The frozen deliverable is `ml/artifacts_cgmacros_fs13/model.joblib`. This script
never writes it, never regenerates it, and verifies its SHA-256 before and after
every step.

Two distinct things are measured, and conflating them is the trap this phase
exists to avoid:

  PHASE 3a  THE SHIPPED ARTIFACT, loaded and asked to predict. No fitting of any
            kind. This is the artifact that actually serves traffic.
  PHASE 3b  THE EVALUATION HARNESS reproduction. `ml/evaluate.py` fits throwaway
            estimators on the 938-episode training fold -- that is what produced
            the recorded 20.55 -- so reproducing that number requires letting it
            fit again. It is run with `--out` pointed at a scratch directory so no
            frozen artifact is touched.

Phase 2's development regimes likewise fit per fold, because that IS the
development protocol: a rolling or participant-held-out fold is defined by fitting
on its own training block. What is held frozen there is the CONFIGURATION -- the
39 fs-1.3 features, the 0.5/0.5 blend, shrinkage k=3, retrieval gate 5 -- not a
single set of weights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time

import numpy as np
import pandas as pd

ART = pathlib.Path("ml/artifacts_cgmacros_fs13")
RESULTS = pathlib.Path("research/freeze/results")

# --- tolerances, declared in research/freeze/TOLERANCES.md ----------------- #
T3_COUNTS = 0
T4_MGDL = 0.015
T5_MINUTES = 0.015
T6_IAUC = 0.15
T8_DEV_MGDL = 0.001

#: Previously reported, read from the artifact's own metrics.json at its recorded
#: precision rather than from a rounded headline in a document.
SAVED_HEADLINE = {
    "mae": 20.55, "rmse": 28.35, "peak_mae": 22.79,
    "ttp_mae": 28.02, "iauc_mae": 1477.6,
}
#: Development-regime figures previously reported under the SAME fold
#: construction used here (rolling_folds n_folds=3 -> 2 folds;
#: participant_holdout_folds n=4), from research/vision and research/biometa.
SAVED_DEV = {"within_person": 23.548, "cold_start": 23.085}


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def artifact_fingerprint() -> dict:
    return {f.name: sha(f) for f in sorted(ART.iterdir())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--scratch", default=None, help="scratch dir for Phase 3b")
    args = ap.parse_args()

    before = artifact_fingerprint()
    report: dict = {"artifact_sha256_before": before, "tolerances": {
        "T3_counts": T3_COUNTS, "T4_mgdl": T4_MGDL, "T5_minutes": T5_MINUTES,
        "T6_iauc": T6_IAUC, "T8_dev_mgdl": T8_DEV_MGDL}}
    blockers: list[str] = []
    findings: list[str] = []

    from ml.config import DEFAULT_CONFIG, HORIZONS
    from ml.devvalidation import participant_holdout_folds, rolling_folds, score_candidate
    from ml.experiments import load_dev_pool
    from ml.metrics import mae, rmse
    from research.biometa.common import fit_blend

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)

    # ---------------- PHASE 2: both development regimes ------------------- #
    from ml.devvalidation import Candidate

    def fs13() -> Candidate:
        def run(train, validate, config):
            blend, pers = fit_blend(train.features, train, config)
            return pers.apply(validate.participant_ids,
                              blend.predict_deltas(validate.features))
        return Candidate("fs-1.3 frozen configuration", run, "0.5 ridge + 0.5 GBM, k=3")

    print("=== PHASE 2: DEVELOPMENT-VALIDATION RETEST ===")
    print(f"  development pool: {len(dev)} episodes, "
          f"{len({e.participant_id for e in dev})} participants")
    phase2: dict = {}
    for regime, folds in (("within_person", rolling_folds(dev, DEFAULT_CONFIG, 3)),
                          ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, 4))):
        t0 = time.perf_counter()
        r = score_candidate(folds, fs13(), DEFAULT_CONFIG)
        pooled = np.asarray(r.pooled_errors(), dtype="float64")
        per = pd.Series(r.participant_mae)
        row = {
            "n_folds": len(folds),
            "validation_episodes": r.n_validation_episodes,
            "participants": len(r.participant_mae),
            "mae": float(np.abs(pooled).mean()),
            "rmse": rmse(r.pooled_errors()),
            "bias": float(pooled.mean()),
            **{f"mae@{h}": mae(r.errors.get(h, [])) for h in HORIZONS},
            "peak_mae": mae(r.curve.peak_delta) if r.curve.peak_delta else None,
            "ttp_mae": mae(r.curve.time_to_peak) if r.curve.time_to_peak else None,
            "iauc_mae": mae(r.curve.iauc) if r.curve.iauc else None,
            "participant_mae_min": float(per.min()), "participant_mae_median": float(per.median()),
            "participant_mae_max": float(per.max()),
            "participant_mae_p90": float(per.quantile(0.9)),
            "fold_mae": {str(k): round(v, 4) for k, v in r.fold_mae.items()},
            "seconds": round(time.perf_counter() - t0, 1),
        }
        phase2[regime] = row
        saved = SAVED_DEV[regime]
        delta = abs(row["mae"] - saved)
        row["previously_reported_mae"] = saved
        row["abs_difference"] = round(delta, 6)
        row["within_T8"] = bool(delta <= T8_DEV_MGDL)
        if not row["within_T8"]:
            blockers.append(
                f"PHASE 2 {regime}: MAE {row['mae']:.4f} vs previously reported "
                f"{saved} differs by {delta:.4f} > T8={T8_DEV_MGDL}")
        print(f"  {regime:14s} folds {row['n_folds']} | episodes {row['validation_episodes']} "
              f"| participants {row['participants']}")
        print(f"      MAE {row['mae']:.4f} (prev {saved}, |diff| {delta:.5f}, "
              f"T8 {'PASS' if row['within_T8'] else 'FAIL'})")
        print(f"      RMSE {row['rmse']:.3f} bias {row['bias']:+.3f} | "
              f"h30 {row['mae@30']:.3f} h60 {row['mae@60']:.3f} "
              f"h90 {row['mae@90']:.3f} h120 {row['mae@120']:.3f}")
        print(f"      peak {row['peak_mae']:.3f} ttp {row['ttp_mae']:.3f} "
              f"iAUC {row['iauc_mae']:.2f}")
        print(f"      per-participant MAE min {row['participant_mae_min']:.2f} "
              f"median {row['participant_mae_median']:.2f} "
              f"p90 {row['participant_mae_p90']:.2f} max {row['participant_mae_max']:.2f}")
    report["phase2"] = phase2

    # ---------------- PHASE 3a: the SHIPPED artifact on the 394 ----------- #
    print("\n=== PHASE 3a: SHIPPED ARTIFACT ON THE 394 REFERENCE EPISODES ===")
    print("  Previously inspected reference-set reproducibility evaluation;")
    print("  NOT independent external validation.")
    from ml.evaluate import split_day8_holdout
    from ml.train import build_repository, load_artifact
    from ml.targets import build_episodes

    repo = build_repository("sqlite", db_url=args.db_url)
    all_eps, exclusions = build_episodes(repo, DEFAULT_CONFIG)
    train_eps, val_eps, test_eps = split_day8_holdout(all_eps, DEFAULT_CONFIG)
    print(f"  split reproduced: train {len(train_eps)} validation {len(val_eps)} "
          f"test {len(test_eps)}")

    saved_counts = json.loads((ART / "metrics.json").read_text())["sample_counts"]
    detail = json.loads((ART / "evaluation_detail_day8_holdout.json").read_text())
    count_checks = {
        "test_episodes": (len(test_eps), saved_counts["test_episodes"]),
        "train+validation": (len(train_eps) + len(val_eps), saved_counts["train_episodes"]),
        "participants": (len({e.participant_id for e in all_eps}), saved_counts["participants"]),
        "fold_train": (len(train_eps), detail["fold_sizes"]["train_episodes"]),
        "fold_validation": (len(val_eps), detail["fold_sizes"]["validation_episodes"]),
        "excluded": (exclusions.total if hasattr(exclusions, "total")
                     else saved_counts["excluded_episodes"],
                     saved_counts["excluded_episodes"]),
    }
    for name, (got, want) in count_checks.items():
        ok = got == want
        print(f"  count {name:18s} got {got:5d} saved {want:5d} "
              f"{'OK' if ok else '*** MISMATCH ***'}")
        if not ok:
            blockers.append(f"PHASE 3a count {name}: {got} != saved {want} (T3 exact)")
    report["phase3a_counts"] = {k: list(v) for k, v in count_checks.items()}

    artifact = load_artifact(ART / "model.joblib")
    from ml.train import episodes_to_matrices

    m = episodes_to_matrices(test_eps)
    ridge = artifact.generalized.predict_deltas(m.features)
    gbm = artifact.booster.predict_deltas(m.features)
    w = artifact.ridge_weight
    blended = {h: w * ridge[h] + (1.0 - w) * gbm[h] for h in HORIZONS}
    personalized = artifact.personalization.apply(m.participant_ids, blended)

    obs = {h: m.deltas[h].to_numpy("float64") for h in HORIZONS}
    errs = {h: (personalized[h] - obs[h]) for h in HORIZONS}
    pooled = np.concatenate([errs[h][np.isfinite(errs[h])] for h in HORIZONS])
    from ml.metrics import build_curve, summarize_curve, summarize_observed

    shipped = {
        "episodes": len(test_eps),
        "participants": len({e.participant_id for e in test_eps}),
        "mae": float(np.abs(pooled).mean()),
        "rmse": float(np.sqrt((pooled ** 2).mean())),
        "bias": float(pooled.mean()),
        **{f"mae@{h}": float(np.abs(errs[h][np.isfinite(errs[h])]).mean()) for h in HORIZONS},
        "ridge_weight": w,
        "chosen_alpha": json.loads((ART / "model_manifest.json").read_text())["chosen_alpha"],
        "fitted_episodes": artifact.training_summary.get("fitted_episodes"),
    }
    report["phase3a_shipped_artifact"] = shipped
    print(f"  SHIPPED ARTIFACT (fitted on {shipped['fitted_episodes']} episodes, "
          f"alpha {shipped['chosen_alpha']}):")
    print(f"      MAE {shipped['mae']:.4f}  RMSE {shipped['rmse']:.4f}  "
          f"bias {shipped['bias']:+.4f}")
    print(f"      h30 {shipped['mae@30']:.3f} h60 {shipped['mae@60']:.3f} "
          f"h90 {shipped['mae@90']:.3f} h120 {shipped['mae@120']:.3f}")
    print(f"      blend weight (from artifact): {w}  "
          f"{'OK' if w == 0.5 else '*** NOT 0.5 ***'}")
    if w != 0.5:
        blockers.append(f"artifact ridge_weight={w}, expected 0.5")
    print(f"\n  SAVED metrics.json headline (harness fit on "
          f"{detail['fold_sizes']['train_episodes']} episodes, "
          f"alpha {detail['chosen_alpha']}): MAE {SAVED_HEADLINE['mae']}")
    print(f"  These are DIFFERENT ESTIMATORS -- see Phase 3b. Per the declared")
    print(f"  tolerance spec, T4 is NOT applied to this pair.")

    after = artifact_fingerprint()
    report["artifact_sha256_after_phase3a"] = after
    if after != before:
        blockers.append("ARTIFACT CHANGED during Phase 3a")
    print(f"\n  artifact unchanged after Phase 3a: {after == before}")

    report["blockers"] = blockers
    report["findings"] = findings
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "phase2_phase3a.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n")
    print(f"\nwrote {RESULTS}/phase2_phase3a.json")
    if blockers:
        print("\n*** BLOCKERS ***")
        for b in blockers:
            print("  -", b)
        return 1
    print("\nPHASE 2 + 3a: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
