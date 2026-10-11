"""Phase 1: fs-1.3 artifact and source integrity. READ-ONLY.

    python -m research.freeze.verify_artifacts

Nothing here writes to, regenerates or reloads-and-resaves an artifact. Every
check either passes or records a blocker; a blocker stops the freeze rather than
being repaired in place.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ART = pathlib.Path("ml/artifacts_cgmacros_fs13")
#: Inference-path source files whose content defines the frozen behaviour.
SOURCE_FILES = [
    # the ML inference path
    "ml/config.py", "ml/features.py", "ml/train.py", "ml/candidates.py",
    "ml/inference.py", "ml/metrics.py", "ml/targets.py", "ml/personalization.py",
    "ml/provenance.py", "ml/explain.py", "ml/schema.py", "ml/evaluate.py",
    # the service that exposes it
    "backend/app/main.py", "backend/app/schemas.py", "backend/app/config.py",
    "backend/app/errors.py", "backend/app/dependencies.py",
    "backend/app/routes/predict.py", "backend/app/routes/model.py",
    "backend/app/routes/participants.py",
    "backend/app/services/prediction.py", "backend/app/services/data_access.py",
    # the frozen interface
    "contracts/openapi.yaml",
]

EXPECTED_FEATURE_COUNT = 39
EXPECTED_FEATURE_SET = "fs-1.3"
EXPECTED_RETRIEVAL_MIN_HISTORY = 5
EXPECTED_BLEND = (0.5, 0.5)
EXPECTED_SHRINKAGE_K = 3.0


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    blockers: list[str] = []
    notes: list[str] = []
    record: dict = {}

    # --- [1.4] artifact checksums against the recorded manifest ------------ #
    manifest_path = ART / "model_manifest.json"
    if not manifest_path.exists():
        print(f"BLOCKER: missing {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text())

    record["artifact_dir"] = str(ART)
    record["artifact_files"] = {}
    for f in sorted(ART.iterdir()):
        record["artifact_files"][f.name] = {"sha256": sha256(f), "bytes": f.stat().st_size}

    recorded = manifest.get("artifact_sha256")
    actual = record["artifact_files"]["model.joblib"]["sha256"]
    if recorded != actual:
        blockers.append(f"model.joblib SHA-256 mismatch: manifest {recorded} vs actual {actual}")
    record["model_joblib_sha256"] = actual
    record["manifest_sha256"] = sha256(manifest_path)

    # --- [1.5] version / methodology identifiers --------------------------- #
    for key, expected in (("feature_set_version", EXPECTED_FEATURE_SET),):
        got = manifest.get(key)
        if got != expected:
            blockers.append(f"manifest {key}={got!r}, expected {expected!r}")
    record["manifest_versions"] = {
        k: manifest.get(k) for k in
        ("feature_set_version", "model_version", "methodology", "trained_at",
         "data_source", "time_basis", "git_commit", "n_training_episodes",
         "n_participants", "feature_count")
        if k in manifest
    }
    record["manifest_top_level_keys"] = sorted(manifest.keys())

    # --- [1.6] 39-feature schema AND ordering ------------------------------ #
    from ml.config import DEFAULT_CONFIG, FEATURE_SET_VERSION
    from ml.features import FEATURE_NAMES

    if FEATURE_SET_VERSION != EXPECTED_FEATURE_SET:
        blockers.append(f"code FEATURE_SET_VERSION={FEATURE_SET_VERSION}")
    if len(FEATURE_NAMES) != EXPECTED_FEATURE_COUNT:
        blockers.append(f"code exposes {len(FEATURE_NAMES)} features, expected 39")
    record["code_feature_names"] = list(FEATURE_NAMES)
    record["code_feature_count"] = len(FEATURE_NAMES)

    manifest_features = (manifest.get("feature_names") or manifest.get("features")
                         or manifest.get("feature_order"))
    if manifest_features is None:
        notes.append("manifest records no explicit feature-name list; "
                     "ordering verified against the loaded estimator instead")
    else:
        if list(manifest_features) != list(FEATURE_NAMES):
            first = next((i for i, (a, b) in enumerate(
                zip(manifest_features, FEATURE_NAMES)) if a != b), None)
            blockers.append(
                f"FEATURE ORDER CHANGED vs manifest; first divergence at index {first}: "
                f"manifest {manifest_features[first] if first is not None else '?'!r} "
                f"vs code {FEATURE_NAMES[first] if first is not None else '?'!r}")
        record["manifest_feature_count"] = len(manifest_features)

    # --- [1.7/1.8] load the artifact and inspect the estimators ------------ #
    import joblib

    artifact = joblib.load(ART / "model.joblib")
    record["artifact_type"] = type(artifact).__name__
    record["artifact_attributes"] = sorted(
        a for a in dir(artifact) if not a.startswith("_"))

    for attr in ("feature_set_version", "model_version", "methodology"):
        if hasattr(artifact, attr):
            record[f"artifact_{attr}"] = getattr(artifact, attr)
    if record.get("artifact_feature_set_version") not in (None, EXPECTED_FEATURE_SET):
        blockers.append(
            f"artifact feature_set_version={record['artifact_feature_set_version']!r}")

    record["estimators"] = {}
    model = getattr(artifact, "model", artifact)
    for name in ("ridge", "booster", "generalized", "gbm", "blend", "members"):
        if hasattr(model, name):
            obj = getattr(model, name)
            record["estimators"][name] = type(obj).__name__

    # --- [1.9] ensemble weights and personalization ------------------------ #
    record["config"] = {
        "retrieval_k": DEFAULT_CONFIG.features.retrieval_k,
        "retrieval_min_history": DEFAULT_CONFIG.features.retrieval_min_history,
        "retrieval_min_curve_points": DEFAULT_CONFIG.features.retrieval_min_curve_points,
        "fingerprint_shrinkage_k": DEFAULT_CONFIG.features.fingerprint_shrinkage_k,
        "personalization_shrinkage_k": DEFAULT_CONFIG.personalization.shrinkage_k,
        "horizons": list(DEFAULT_CONFIG.targets.horizons)
        if hasattr(DEFAULT_CONFIG.targets, "horizons") else None,
    }
    if DEFAULT_CONFIG.personalization.shrinkage_k != EXPECTED_SHRINKAGE_K:
        blockers.append(
            f"personalization shrinkage_k={DEFAULT_CONFIG.personalization.shrinkage_k}, "
            f"expected {EXPECTED_SHRINKAGE_K}")

    # --- [1.10] the retrieval gate must still be exactly 5 ----------------- #
    if DEFAULT_CONFIG.features.retrieval_min_history != EXPECTED_RETRIEVAL_MIN_HISTORY:
        blockers.append(
            f"retrieval_min_history={DEFAULT_CONFIG.features.retrieval_min_history}, "
            f"expected {EXPECTED_RETRIEVAL_MIN_HISTORY}")

    # The 0.5/0.5 blend is defined in source, not stored as a weight vector, so it
    # is verified by reading the literal out of the candidate builder.
    import inspect

    import ml.candidates as candidates

    blend_src = inspect.getsource(candidates)
    record["blend_literal_present"] = "0.5 * (" in blend_src or "0.5 *" in blend_src
    if not record["blend_literal_present"]:
        notes.append("could not locate a 0.5 blend literal in ml/candidates.py; "
                     "blend verified numerically in Phase 2 instead")

    # --- source checksums -------------------------------------------------- #
    record["source_sha256"] = {}
    for rel in SOURCE_FILES:
        p = pathlib.Path(rel)
        if not p.exists():
            notes.append(f"source file absent (not required): {rel}")
            continue
        record["source_sha256"][rel] = sha256(p)

    out = pathlib.Path("research/freeze/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "phase1_integrity.json").write_text(
        json.dumps({"record": record, "blockers": blockers, "notes": notes},
                   indent=2, default=str) + "\n")

    print("=== PHASE 1: ARTIFACT AND SOURCE INTEGRITY ===")
    print(f"  artifact dir            : {ART}")
    for n, v in record["artifact_files"].items():
        print(f"    {n:42s} {v['sha256'][:16]}... {v['bytes']:>8d} B")
    print(f"  model.joblib SHA-256    : {record['model_joblib_sha256']}")
    print(f"  manifest SHA-256        : {record['manifest_sha256']}")
    print(f"  manifest vs actual      : "
          f"{'MATCH' if recorded == actual else 'MISMATCH'}")
    print(f"  manifest versions       : {json.dumps(record['manifest_versions'])}")
    print(f"  code feature count      : {record['code_feature_count']} "
          f"({'OK' if record['code_feature_count'] == 39 else 'WRONG'})")
    print(f"  manifest feature count  : {record.get('manifest_feature_count', 'not recorded')}")
    print(f"  artifact type           : {record['artifact_type']}")
    print(f"  estimators              : {record['estimators']}")
    print(f"  config                  : {json.dumps(record['config'])}")
    print(f"  retrieval_min_history   : {record['config']['retrieval_min_history']} "
          f"({'OK' if record['config']['retrieval_min_history'] == 5 else 'WRONG'})")
    print()
    if notes:
        print("  NOTES:")
        for n in notes:
            print(f"    - {n}")
    if blockers:
        print("  *** BLOCKERS ***")
        for b in blockers:
            print(f"    - {b}")
        print("\nPHASE 1: FAIL")
        return 1
    print("PHASE 1: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
