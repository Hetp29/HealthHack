"""The real demonstration: genuine CGMacros recording, frozen fs-1.3 model.

Skipped -- never passed vacuously -- when the bundle or the artifact is absent;
neither is in git.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.app.v2.demo import source

ARTIFACT = pathlib.Path("ml/artifacts_cgmacros_fs13/model.joblib")
BUNDLE = pathlib.Path("data/local/demo/cgmacros_demo_bundle.json")
available = pytest.mark.skipif(
    not (ARTIFACT.exists() and BUNDLE.exists()),
    reason="needs the frozen artifact and the demo data bundle (run `python -m scripts.demo_select`)")


RETURNING_BUNDLE = pathlib.Path("data/local/demo/cgmacros_returning_user_bundle.json")
returning_available = pytest.mark.skipif(
    not (ARTIFACT.exists() and RETURNING_BUNDLE.exists()),
    reason="needs the frozen artifact and the returning-user bundle "
           "(run `python -m scripts.demo_select --scenario returning_user`)")


def test_manifest_is_committed_metadata_without_research_values():
    manifest = source.load_manifest()
    assert manifest["data_class"] == "research_dataset"
    assert manifest["selection"]["inputs_not_consulted"] == ["glucose values", "forecasts", "forecast errors"]
    assert "not independent validation" in manifest["fit_relationship"]["statement"]
    text = json.dumps(manifest)
    for forbidden in ("glucose_mg_dl", "carbs_g", "calories_kcal", "\"cgm\""):
        assert forbidden not in text
    assert all(set(m) == {"number", "source_meal_id", "occurred_at", "readings_in_window", "expected_eligible",
                          "expected_ineligibility_reasons", "expected_admissible_prior_meals"}
               for m in manifest["meals"])
    assert manifest["plan"]["activation_verified_by_replay"] is True


def test_no_research_data_file_is_tracked_by_git():
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.splitlines()
    assert not [p for p in tracked if p.startswith("data/local/") or p.endswith("demo_bundle.json")]
    ignored = subprocess.run(["git", "check-ignore", str(BUNDLE)], capture_output=True, text=True)
    assert ignored.returncode == 0


@available
def test_bundle_is_the_selected_recording_and_readings_are_untouched():
    manifest = source.load_manifest()
    bundle = source.load_bundle(BUNDLE, expected_sha256=manifest["bundle_sha256"])
    assert bundle.data_class == "research_dataset" and bundle.store_labels == ("research_cgmacros", "research_dataset")
    assert len(bundle.meals) == manifest["window"]["meals"] and len(bundle.cgm) == manifest["window"]["readings"]
    assert [m.source_meal_id for m in bundle.meals] == [m["source_meal_id"] for m in manifest["meals"]]
    times = [t for t, _ in bundle.cgm]
    assert times == sorted(times) and len(set(times)) == len(times)

    warehouse = pathlib.Path("data/local/cgmacros_v2.db")
    if warehouse.exists():
        from data.storage.repository import SqliteDataRepository
        history = SqliteDataRepository(f"sqlite:///{warehouse}").get_history(manifest["source"]["participant"])
        original = {p.timestamp: p.glucose_mg_dl for p in history.cgm}
        assert all(original[t] == v for t, v in bundle.cgm)          # value-for-value the warehouse's
        meals = {m.meal_id: m for m in history.meals}
        for meal in bundle.meals:
            assert meal.occurred_at == meals[meal.source_meal_id].timestamp
            assert meal.macros == {k: getattr(meals[meal.source_meal_id], k) for k in source.MACROS}


@available
def test_selection_is_reproducible_from_the_bundle_alone():
    manifest = source.load_manifest()
    bundle = source.load_bundle(BUNDLE, expected_sha256=manifest["bundle_sha256"])
    rows = [(m.source_meal_id, m.occurred_at, m.macros) for m in bundle.meals]
    assessed = source.assess_meals([t for t, _ in bundle.cgm], rows, [m.occurred_at for m in bundle.meals])
    assert [a["expected_eligible"] for a in assessed] == [m["expected_eligible"] for m in manifest["meals"]]
    assert [source.expected_admissible(assessed, i) for i in range(len(assessed))] == \
        [m["expected_admissible_prior_meals"] for m in manifest["meals"]]


@available
def test_full_demonstration_verifies_on_real_data_with_the_frozen_model(capsys):
    from scripts import demo_verify

    code = demo_verify.main(["--scenario", "new_user"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "ALL CHECKS PASSED" in output and "FAIL" not in output
    assert output.count("PASS") >= 26
    # The scenario's behaviour is exactly what it was when it was first verified.
    assert "fingerprint matches" in output or "NOTE" not in output


@returning_available
def test_returning_user_demonstration_verifies_on_real_data_with_the_frozen_model(capsys):
    from scripts import demo_verify

    code = demo_verify.main(["--scenario", "returning_user"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "FAIL" not in output
    assert "personal history is already active at the first presented meal" in output
    assert "every backfilled meal went through a real forecast" in output


def test_returning_user_manifest_records_the_gate_and_the_fit_relationship():
    manifest = source.load_manifest(source.manifest_path("returning_user"))
    base = source.load_manifest(source.manifest_path("new_user"))
    assert manifest["mode"] == "returning_user" and base["mode"] == "new_user"
    assert manifest["source"]["participant"] == base["source"]["participant"]
    backfill = manifest["backfill"]
    assert backfill["meals_found"] == 31 and backfill["eligible"] == 27 and backfill["unloggable"] == 0
    assert backfill["ineligibility_reasons"] == {"intervening_meal": 4}
    assert backfill["admissible_at_first_presented_meal"] >= 5          # the gate this scenario had to pass
    assert manifest["plan"]["retrieval_active_at_first_presented_meal"] is True
    assert manifest["plan"]["activation_meal"] == 6                     # only after five eligible meals
    assert "WERE in the frozen artifact's fit" in manifest["fit_relationship"]["statement"]
    assert "independent validation" in manifest["fit_relationship"]["statement"]
    # The presented meals are exactly the new-user scenario's nine, in order.
    shown = [m for m in manifest["meals"] if m["phase"] == "display"]
    assert [m["source_meal_id"] for m in shown] == [m["source_meal_id"] for m in base["meals"]]
    assert all(m["phase"] == "backfill" for m in manifest["meals"][:31])
    text = json.dumps(manifest)
    for forbidden in ("glucose_mg_dl", "carbs_g", "calories_kcal", "\"cgm\""):
        assert forbidden not in text


@returning_available
def test_returning_user_bundle_is_every_earlier_meal_untouched():
    manifest = source.load_manifest(source.manifest_path("returning_user"))
    bundle = source.load_bundle(RETURNING_BUNDLE, expected_sha256=manifest["bundle_sha256"])
    assert bundle.data_class == "research_dataset" and len(bundle.meals) == 40
    warehouse = pathlib.Path("data/local/cgmacros_v2.db")
    if warehouse.exists():
        from data.storage.repository import SqliteDataRepository
        history = SqliteDataRepository(f"sqlite:///{warehouse}").get_history(manifest["source"]["participant"])
        # Every meal the recording holds up to the final presented meal -- none dropped.
        expected = [m for m in history.meals if m.timestamp <= bundle.meals[-1].occurred_at]
        assert [m.meal_id for m in expected] == [m.source_meal_id for m in bundle.meals]
        for mine, theirs in zip(bundle.meals, expected):
            assert mine.occurred_at == theirs.timestamp
            assert mine.macros == {k: getattr(theirs, k) for k in source.MACROS}
        original = {p.timestamp: p.glucose_mg_dl for p in history.cgm}
        assert all(original[t] == v for t, v in bundle.cgm)


@returning_available
def test_the_new_user_bundle_and_manifest_were_not_changed_by_the_returning_scenario():
    base = source.load_manifest(source.manifest_path("new_user"))
    assert base["bundle_sha256"] == "4ef9dbd8193cafee700ff8599059346928262ef8baa05c19ee8287196e1efe22"
    assert base["scenario_id"] == "cgmacros-out-of-fit-replay-v1"
    assert [m["expected_admissible_prior_meals"] for m in base["meals"]] == [0, 1, 1, 2, 3, 4, 5, 5, 6]
    source.load_bundle(BUNDLE, expected_sha256=base["bundle_sha256"])
