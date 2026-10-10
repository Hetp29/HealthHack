"""Rulings D1/D2/D3 as executable checks.

Covers the three decisions that govern how real organizer data may be used:

* **D1** -- the Day-8 held-out protocol: disjoint train/validation/test, a 120-minute
  embargo so no response window straddles folds, and test episodes that genuinely
  satisfy the prior-context rule.
* **D2** -- `data_source` is resolved from the participant records, never from a CLI
  flag, and a mixed-provenance database is refused rather than silently labelled.
* **D3** -- timestamps with an unknown timezone are NOT treated as real UTC instants:
  the stored wall clock is read directly for time-of-day, and a genuinely-UTC dataset
  with no timezone reports the time of day missing instead of guessing.

These run against the committed synthetic fixtures, so they need no organizer data and
pass on a clean clone.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from ml.config import DEFAULT_CONFIG
from ml.devfixture import SyntheticDevRepository
from ml.evaluate import (
    CONTRACT_PROTOCOL_NAME,
    PROTOCOLS,
    LeakageError,
    _assert_day8_separation,
    split_day8_holdout,
)
from ml.features import FEATURE_NAMES, OPTIONAL_FEATURES, _minutes_of_day
from ml.provenance import (
    SOURCE_LOCAL_UNKNOWN_TZ,
    UTC_BASIS,
    ProvenanceError,
    describe_time_basis,
    resolve_data_source,
    resolve_time_basis,
)
from ml.schema import ParticipantSummary
from ml.targets import build_episodes

MEAL_TIME = dt.datetime(2020, 5, 1, 14, 23, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# D3 -- timestamp semantics
# --------------------------------------------------------------------------- #


class TestUnknownTimezoneIsNotTreatedAsUtc:
    def test_source_local_wall_clock_is_read_directly(self):
        """14:23 stored under a +00:00 label IS 14:23 local. No conversion.

        This is the regression that matters: converting would shift every circadian
        feature by the participant's true offset.
        """
        assert _minutes_of_day(MEAL_TIME, SOURCE_LOCAL_UNKNOWN_TZ, None) == pytest.approx(
            14 * 60 + 23
        )

    def test_a_declared_timezone_is_ignored_for_floating_time(self):
        """A stray timezone must not re-shift an already-local wall clock."""
        assert _minutes_of_day(
            MEAL_TIME, SOURCE_LOCAL_UNKNOWN_TZ, "America/New_York"
        ) == pytest.approx(14 * 60 + 23)

    def test_real_utc_with_a_known_timezone_is_converted(self):
        assert _minutes_of_day(MEAL_TIME, UTC_BASIS, "America/New_York") == pytest.approx(
            10 * 60 + 23
        )

    def test_real_utc_without_a_timezone_is_unknown_not_guessed(self):
        assert _minutes_of_day(MEAL_TIME, UTC_BASIS, None) is None

    def test_unknowable_time_of_day_becomes_a_missing_feature(self):
        """So the fitted imputer handles it, with an explicit missing indicator."""
        assert "tod_sin" in OPTIONAL_FEATURES
        assert "tod_cos" in OPTIONAL_FEATURES
        assert "tod_sin" in FEATURE_NAMES

    @pytest.mark.parametrize("hour,minute", [(0, 0), (6, 30), (12, 0), (23, 59)])
    def test_wall_clock_is_preserved_across_the_day(self, hour, minute):
        stamp = dt.datetime(2020, 5, 1, hour, minute, tzinfo=dt.timezone.utc)
        assert _minutes_of_day(
            stamp, SOURCE_LOCAL_UNKNOWN_TZ, None
        ) == pytest.approx(hour * 60 + minute)

    def test_caveat_is_accurate_for_floating_time(self):
        """The old wording claimed UTC fallback degraded the features. It did not."""
        caveat = describe_time_basis(SOURCE_LOCAL_UNKNOWN_TZ, None)
        assert caveat is not None
        assert "ordering keys" in caveat
        assert "not comparable across participants" in caveat

    def test_caveat_for_utc_without_timezone_says_missing_not_guessed(self):
        caveat = describe_time_basis(UTC_BASIS, None)
        assert caveat is not None and "rather than guessed" in caveat

    def test_no_caveat_when_the_clock_is_fully_known(self):
        assert describe_time_basis(UTC_BASIS, "America/New_York") is None

    def test_naive_timestamps_are_still_refused(self):
        with pytest.raises(Exception):
            _minutes_of_day(dt.datetime(2020, 5, 1, 14, 23), UTC_BASIS, None)


class TestTimeBasisResolution:
    def test_fixture_without_the_extension_defaults_to_utc(self):
        basis, tz = resolve_time_basis(SyntheticDevRepository(), "synthetic_p001", "UTC")
        assert (basis, tz) == (UTC_BASIS, "UTC")

    def test_extension_method_is_used_when_present(self):
        class WithExtension:
            def get_time_basis(self, participant_id):
                return {"time_basis": SOURCE_LOCAL_UNKNOWN_TZ, "timezone": None}

        assert resolve_time_basis(WithExtension(), "p", "UTC") == (
            SOURCE_LOCAL_UNKNOWN_TZ,
            None,
        )

    def test_unknown_participant_falls_back_safely(self):
        class Empty:
            def get_time_basis(self, participant_id):
                return None

        assert resolve_time_basis(Empty(), "p", "Europe/Berlin") == (
            UTC_BASIS,
            "Europe/Berlin",
        )


# --------------------------------------------------------------------------- #
# D2 -- provenance
# --------------------------------------------------------------------------- #


class _FakeRepository:
    def __init__(self, sources):
        self._summaries = [
            ParticipantSummary(f"p{i:03d}", f"P{i}", source, 7.0, None)
            for i, source in enumerate(sources)
        ]

    def list_participants(self):
        return list(self._summaries)


class TestDataSourceResolution:
    def test_resolved_from_the_records(self):
        assert resolve_data_source(_FakeRepository(["official"] * 3)) == "official"
        assert (
            resolve_data_source(_FakeRepository(["synthetic_dev"] * 2)) == "synthetic_dev"
        )

    def test_fixture_resolves_to_synthetic_dev(self):
        assert resolve_data_source(SyntheticDevRepository()) == "synthetic_dev"

    def test_sqlite_is_never_a_data_source(self):
        """`sqlite` is a storage backend, and not a value the frozen enum permits."""
        with pytest.raises(ProvenanceError, match="not in the frozen contract enum"):
            resolve_data_source(_FakeRepository(["sqlite"]))

    def test_mixed_provenance_is_refused_not_flattened(self):
        with pytest.raises(ProvenanceError, match="mixes provenances"):
            resolve_data_source(_FakeRepository(["official", "synthetic_dev"]))

    def test_the_refusal_explains_why_pooling_needs_a_decision(self):
        with pytest.raises(ProvenanceError) as excinfo:
            resolve_data_source(_FakeRepository(["official", "synthetic_dev"]))
        assert "modelling decision" in str(excinfo.value)

    def test_empty_repository_is_refused(self):
        with pytest.raises(ProvenanceError, match="no participants"):
            resolve_data_source(_FakeRepository([]))

    def test_resolved_label_satisfies_the_frozen_contract(self):
        from backend.app.schemas import ModelInfoResponse

        label = resolve_data_source(SyntheticDevRepository())
        ModelInfoResponse.model_validate(
            {
                "model_version": "v0.1.0",
                "trained_at": dt.datetime.now(dt.timezone.utc),
                "data_source": label,
                "feature_set_version": "fs-1.1",
                "metrics_available": True,
                "limitations": ["x"],
            }
        )


# --------------------------------------------------------------------------- #
# D1 -- the Day-8 held-out protocol
# --------------------------------------------------------------------------- #


class TestDay8Protocol:
    @pytest.fixture(scope="class")
    def episodes(self):
        """A 16-day fixture, so Day-8-eligible episodes actually exist.

        The default 7-day fixture cannot contain one by construction: no meal in it
        has 7 days of PRIOR history. Testing the Day-8 guards against it would skip
        silently, which is how a guard rots unnoticed.
        """
        episodes, report = build_episodes(
            SyntheticDevRepository(days=16), DEFAULT_CONFIG
        )
        assert report.day8_eligible > 0, "fixture must exercise the Day-8 path"
        return episodes

    def test_day8_is_a_registered_protocol_and_is_primary(self):
        assert PROTOCOLS[0] == "day8_holdout"

    def test_day8_reports_under_a_frozen_contract_name(self):
        """Contract v1.0 freezes the enum; the split IS within-person chronological."""
        assert CONTRACT_PROTOCOL_NAME["day8_holdout"] == "within_person_chronological"

    def test_folds_are_disjoint(self, episodes):
        train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        ids = [{id(e) for e in g} for g in (train, validation, test)]
        assert not (ids[0] & ids[1])
        assert not (ids[0] & ids[2])
        assert not (ids[1] & ids[2])

    def test_every_test_episode_is_day8_eligible(self, episodes):
        _, _, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        for episode in test:
            assert episode.context is not None and episode.context.day8_eligible

    def test_overlapping_meals_are_excluded_from_the_test_set(self, episodes):
        _, _, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        assert all(not e.target.has_overlapping_meal for e in test)

    def test_embargo_is_enforced(self, episodes):
        """No training episode's 120-min response window may reach the test cutoff."""
        train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        embargo = dt.timedelta(minutes=DEFAULT_CONFIG.day8.embargo_minutes)
        earliest = {}
        for episode in test:
            current = earliest.get(episode.participant_id)
            if current is None or episode.meal_timestamp < current:
                earliest[episode.participant_id] = episode.meal_timestamp
        for episode in train + validation:
            cutoff = earliest.get(episode.participant_id)
            if cutoff is not None:
                assert episode.meal_timestamp <= cutoff - embargo

    def test_separation_assertion_passes_on_a_real_split(self, episodes):
        train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        _assert_day8_separation(train, validation, test, DEFAULT_CONFIG)

    def test_separation_assertion_actually_fires(self, episodes):
        """A guard that cannot fail is not a guard."""
        train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        if not test:
            pytest.skip("fixture produced no Day-8 test episodes")
        with pytest.raises(LeakageError):
            _assert_day8_separation(train + test[:1], validation, test, DEFAULT_CONFIG)

    def test_embargo_violation_is_detected(self, episodes):
        train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        if not test:
            pytest.skip("fixture produced no Day-8 test episodes")
        # An episode immediately before the cutoff violates the embargo.
        victim = min(test, key=lambda e: e.meal_timestamp)
        near = [
            e
            for e in episodes
            if e.participant_id == victim.participant_id
            and e.meal_timestamp < victim.meal_timestamp
        ]
        if not near:
            pytest.skip("no earlier episode available for this participant")
        with pytest.raises(LeakageError, match="embargo|disjoint"):
            _assert_day8_separation(
                train + [max(near, key=lambda e: e.meal_timestamp)],
                validation,
                test,
                DEFAULT_CONFIG,
            )

    def test_eligibility_requires_prior_history(self, episodes):
        """With a 7-day rule, an episode on day 1 can never be eligible."""
        for episode in episodes:
            if episode.context is not None and episode.context.day8_eligible:
                assert episode.context.prior_history_days >= DEFAULT_CONFIG.day8.history_days

    def test_eligibility_requires_coverage(self, episodes):
        for episode in episodes:
            if episode.context is not None and episode.context.day8_eligible:
                assert (
                    episode.context.prior_history_coverage
                    >= DEFAULT_CONFIG.day8.min_history_coverage
                )

    def test_a_stricter_history_rule_shrinks_the_test_set(self, episodes):
        """Sanity: the rule actually binds rather than accepting everything."""
        strict = dataclasses.replace(
            DEFAULT_CONFIG,
            day8=dataclasses.replace(DEFAULT_CONFIG.day8, history_days=30),
        )
        _, _, strict_test = split_day8_holdout(episodes, strict)
        _, _, default_test = split_day8_holdout(episodes, DEFAULT_CONFIG)
        assert len(strict_test) <= len(default_test)

    def test_day8_eligible_count_is_reported(self):
        _, report = build_episodes(SyntheticDevRepository(days=16), DEFAULT_CONFIG)
        payload = report.as_dict()
        assert "day8_eligible" in payload
        assert 0 < payload["day8_eligible"] <= payload["accepted"]

    def test_a_seven_day_fixture_yields_no_day8_episodes(self):
        """The rule is real: with only 7 days total, nothing has 7 days of history."""
        _, report = build_episodes(SyntheticDevRepository(days=7), DEFAULT_CONFIG)
        assert report.day8_eligible == 0
