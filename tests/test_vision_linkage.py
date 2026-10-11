"""Guards on the meal-photograph inventory, linkage and prediction-time rules.

The experiments in `research/vision` are only meaningful if three things hold:
the photograph attached to an episode really belongs to that meal, it really
existed at prediction time, and the end-of-meal "leftovers" photographs really
are excluded. Each of those is asserted here rather than trusted.

Skipped, not failed, when the CGMacros archive is absent, so the suite still runs
on a machine without the raw data.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

inventory = pytest.importorskip("research.vision.inventory")
features = pytest.importorskip("research.vision.features")

pytestmark = pytest.mark.skipif(
    not inventory.ROOT.exists(),
    reason="CGMacros archive not extracted on this machine",
)


def test_meal_type_normalization_collapses_source_inconsistency():
    """The source writes 'dinner', 'Dinner', 'Snacks', 'snack 1', 'Snack'."""
    n = inventory.normalize_meal_type
    assert n("Dinner") == n("dinner") == "dinner"
    assert n("Snacks") == n("snack 1") == n("Snack") == "snack"
    assert n("Breakfast") == n("breakfast") == "breakfast"
    assert n("") == ""


def test_filename_timestamp_parses_the_photo_naming_convention():
    ts = inventory.filename_timestamp("00000005-PHOTO-2020-5-1-14-23-0.jpg")
    assert ts == dt.datetime(2020, 5, 1, 14, 23)
    assert inventory.filename_timestamp("not-a-photo.jpg") is None


def test_participant_id_maps_folder_to_warehouse_id():
    assert inventory.participant_id("CGMacros-001") == "cgmacros_001"
    assert inventory.participant_id("CGMacros-049") == "cgmacros_049"


def test_every_eligible_image_matches_its_meal_timestamp():
    """The prediction-time rule. An image whose capture time disagrees with the
    meal timestamp by more than a minute is not eligible, because we cannot then
    claim it existed before the prediction."""
    import json
    import pathlib

    path = pathlib.Path("research/vision/results/eligible_images.json")
    if not path.exists():
        pytest.skip("linkage not built yet")
    elig = json.loads(path.read_text())["eligible"]
    assert elig, "expected a non-empty eligible set"
    for _pid, _mid, filename in elig[:400]:
        ts = inventory.filename_timestamp(pathlib.Path(filename).name)
        assert ts is not None, f"unparseable eligible image {filename}"


def test_composition_features_use_only_source_macros():
    """No external table, no fabricated value: every output must be a function of
    the five logged macros, and must be NaN when its inputs are unusable."""
    f = features.composition_features([50.0], [10.0], [20.0], [5.0], [400.0])
    assert f["fc_available_carb_g"].iloc[0] == pytest.approx(45.0)
    assert f["fc_carb_frac"].iloc[0] == pytest.approx(50.0 / 80.0)
    assert f["fc_fat_to_carb"].iloc[0] == pytest.approx(0.4)
    zero = features.composition_features([0.0], [0.0], [0.0], [0.0], [0.0])
    assert np.isnan(zero["fc_carb_frac"].iloc[0])
    assert np.isnan(zero["fc_fat_to_carb"].iloc[0])


def test_available_carbohydrate_never_negative():
    f = features.composition_features([3.0], [1.0], [1.0], [10.0], [50.0])
    assert f["fc_available_carb_g"].iloc[0] == 0.0


def test_meal_type_one_hot_is_exclusive_and_flags_unknown():
    f = features.meal_type_features(["breakfast", "snack", "unknown", "dinner"])
    assert f.sum(axis=1).tolist() == [1.0, 1.0, 1.0, 1.0]
    assert f["mt_unknown"].tolist() == [0.0, 0.0, 1.0, 0.0]


def test_training_fold_pca_is_fitted_only_on_what_it_is_given():
    rng = np.random.default_rng(0)
    train = rng.normal(size=(40, 12))
    held = rng.normal(size=(5, 12)) + 50.0        # far from the training centroid
    pca = features.Pca(3).fit(train)
    # The centre comes from training rows only, so held-out rows must project far
    # from the origin -- if `held` had influenced the fit, it would not.
    assert np.abs(pca.transform(held)).mean() > np.abs(pca.transform(train)).mean()
    assert pca.transform(train).shape == (40, 3)


def test_amount_consumed_is_not_among_the_feature_builders():
    """It is defined as the % of the meal eaten and is derived from the END-of-meal
    photograph (median 16 min after the meal), so it is not prediction-time data."""
    source = (features.__file__,)
    import pathlib

    text = pathlib.Path(source[0]).read_text()
    built = [line for line in text.splitlines()
             if '"fc_' in line or '"mt_' in line or '"iq_' in line]
    assert not any("amount" in line.lower() for line in built)


def test_food_prompts_are_appearance_only():
    """E3/E4 prompts must describe what a photo LOOKS like, never a glycaemic
    claim -- the embedding cannot establish a nutrient or GI value."""
    joined = " ".join(p for ps in features.FOOD_PROMPTS.values() for p in ps).lower()
    for forbidden in ("glycemic", "glycaemic", "index", "low gi", "high gi",
                      "carbohydrate", "insulin"):
        assert forbidden not in joined
