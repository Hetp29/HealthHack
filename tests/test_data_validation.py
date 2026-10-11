import pytest

from data.validation import (
    ValidationContext,
    validate_activity,
    validate_cgm,
    validate_heart_rate,
    validate_meal,
    validate_medication,
    validate_participant,
)

CTX = ValidationContext(data_source="synthetic_dev")
TS = "2026-10-08T12:30:00Z"


def cgm(value, **kw):
    return validate_cgm({"participant_id": "p1", "timestamp": TS, "glucose_mg_dl": value, **kw}, CTX)


def meal(**macros):
    return validate_meal({"meal_id": "m1", "participant_id": "p1", "timestamp": TS, **macros}, CTX)


# ----------------------------------------------------------------------------- CGM


def test_valid_cgm_is_accepted_clean():
    r = cgm("105")
    assert not r.rejected and not r.warnings
    assert r.record["glucose_mg_dl"] == 105.0
    assert r.record["timestamp"].isoformat() == "2026-10-08T12:30:00+00:00"


@pytest.mark.parametrize(
    "value,reason",
    [("-5", "negative_glucose"), ("0", "zero_glucose"), ("nan", "nonfinite_glucose_mg_dl"),
     ("inf", "nonfinite_glucose_mg_dl"), ("abc", "non_numeric_glucose_mg_dl"), ("", "missing_glucose_mg_dl"),
     ("2000", "implausible_glucose_high")],
)
def test_invalid_glucose_is_rejected(value, reason):
    r = cgm(value)
    assert r.rejected and reason in r.reasons and r.record is None


@pytest.mark.parametrize("value,warning", [("35", "glucose_below_typical_cgm_range"),
                                           ("450", "glucose_above_typical_cgm_range")])
def test_plausible_extremes_are_kept_and_flagged(value, warning):
    r = cgm(value)
    assert not r.rejected and warning in r.warnings


def test_missing_participant_and_bad_timestamp_rejected():
    r = validate_cgm({"participant_id": "", "timestamp": "2026-10-08T12:30:00", "glucose_mg_dl": "100"}, CTX)
    assert set(r.reasons) == {"missing_participant_id", "naive_timestamp"}


# ------------------------------------------------------------------- heart rate


def test_heart_rate_rules():
    ok = validate_heart_rate({"participant_id": "p1", "timestamp": TS, "heart_rate_bpm": "72"}, CTX)
    assert not ok.rejected and ok.record["resting_heart_rate_bpm"] is None  # optional stays null, not 0
    assert "implausible_heart_rate" in validate_heart_rate(
        {"participant_id": "p1", "timestamp": TS, "heart_rate_bpm": "400"}, CTX).reasons
    assert "implausible_heart_rate" in validate_heart_rate(
        {"participant_id": "p1", "timestamp": TS, "heart_rate_bpm": "5"}, CTX).reasons
    flagged = validate_heart_rate({"participant_id": "p1", "timestamp": TS, "heart_rate_bpm": "230"}, CTX)
    assert not flagged.rejected and "heart_rate_outside_typical_range" in flagged.warnings
    assert "nonfinite_heart_rate_bpm" in validate_heart_rate(
        {"participant_id": "p1", "timestamp": TS, "heart_rate_bpm": "inf"}, CTX).reasons


# ------------------------------------------------------------------------ meals


def test_meal_missing_macros_stay_null_with_warning():
    r = meal(carbs_g="40")
    assert not r.rejected
    assert r.record["carbs_g"] == 40.0
    assert r.record["protein_g"] is None and r.record["calories_kcal"] is None
    assert "meal_nutrition_incomplete" in r.warnings
    assert "meal_has_no_nutrition" in meal().warnings


@pytest.mark.parametrize("field", ["carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"])
def test_negative_macros_rejected(field):
    r = meal(**{field: "-1"})
    assert r.rejected and f"negative_{field}" in r.reasons


def test_energy_cross_check_and_fiber_flag():
    r = meal(carbs_g="50", protein_g="20", fat_g="10", fiber_g="60", calories_kcal="900")
    assert not r.rejected
    assert "calories_inconsistent_with_macros" in r.warnings and "fiber_exceeds_carbs" in r.warnings
    clean = meal(carbs_g="50", protein_g="20", fat_g="10", fiber_g="5", calories_kcal="370")
    assert clean.warnings == []


def test_unsafe_image_ref_is_dropped_with_warning():
    for ref in ("../../etc/passwd", "/abs/path.jpg", "C:/x.jpg"):
        r = meal(carbs_g="10", image_ref=ref)
        assert r.record["image_ref"] is None and "unsafe_image_ref_dropped" in r.warnings
    assert meal(carbs_g="10", image_ref="images/m1.jpg").record["image_ref"] == "images/m1.jpg"


# ------------------------------------------------------------------ medications


def test_medication_dose_rules():
    base = {"participant_id": "p1", "timestamp": TS, "medication_class": "biguanide"}
    ok = validate_medication({**base, "dose": "500", "dose_unit": "mg"}, CTX)
    assert not ok.rejected and ok.record["dose"] == 500.0 and ok.record["dose_unit"] == "mg"
    assert ok.record["id"].startswith("med_")  # deterministic id when the source has none
    assert ok.record["id"] == validate_medication({**base, "dose": "500", "dose_unit": "mg"}, CTX).record["id"]
    assert "dose_without_unit" in validate_medication({**base, "dose": "500"}, CTX).reasons
    assert "negative_dose" in validate_medication({**base, "dose": "-1", "dose_unit": "mg"}, CTX).reasons
    assert "nonfinite_dose" in validate_medication({**base, "dose": "nan", "dose_unit": "mg"}, CTX).reasons
    assert "missing_medication_class" in validate_medication({**base, "medication_class": ""}, CTX).reasons
    no_dose = validate_medication(base, CTX)
    assert not no_dose.rejected and no_dose.record["dose"] is None and no_dose.record["dose_unit"] is None


def test_dose_units_are_never_converted():
    ctx = ValidationContext(data_source="official", unit_factors={"glucose_mg_dl": 18.0182})
    r = validate_medication({"participant_id": "p1", "timestamp": TS, "medication_class": "basal_insulin",
                             "dose": "14", "dose_unit": "units"}, ctx)
    assert r.record["dose"] == 14.0 and r.record["dose_unit"] == "units"


# --------------------------------------------------------------- activity, roster


def test_activity_rules():
    base = {"participant_id": "p1", "timestamp": TS}
    assert not validate_activity({**base, "activity_value": "500", "activity_unit": "steps"}, CTX).rejected
    assert "activity_value_without_unit" in validate_activity({**base, "activity_value": "500"}, CTX).reasons
    assert "negative_activity_value" in validate_activity({**base, "activity_value": "-3", "activity_unit": "steps"}, CTX).reasons
    assert "empty_activity_record" in validate_activity(base, CTX).reasons


def test_participant_rules():
    ok = validate_participant({"participant_id": "p1", "display_name": "Participant 001", "timezone": "America/New_York"}, CTX)
    assert ok.record["timezone"] == "America/New_York"
    bad_tz = validate_participant({"participant_id": "p1", "display_name": "P", "timezone": "Mars/Base"}, CTX)
    assert bad_tz.record["timezone"] is None and "invalid_timezone_set_null" in bad_tz.warnings
    assert "missing_participant_id" in validate_participant({"participant_id": " ", "display_name": "P"}, CTX).reasons


def test_unit_conversion_is_applied_only_when_declared():
    ctx = ValidationContext(data_source="official", unit_factors={"glucose_mg_dl": 18.0182})
    r = validate_cgm({"participant_id": "p1", "timestamp": TS, "glucose_mg_dl": "5.5"}, ctx)
    assert r.record["glucose_mg_dl"] == pytest.approx(99.1, abs=0.01)
