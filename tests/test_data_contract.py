"""The data-layer boundary.

Checks the `DataRepository` Protocol that `backend/app/services/data_access.py`
declares, using the temporary dev fixture as the reference implementation.

FOR HET: point `candidate` at your repository and this suite becomes an acceptance
test for the canonical data layer. Nothing here imports anything from `data/`, so it
runs today and keeps running once the real layer lands.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.app.config import Settings, load_settings
from backend.app.services.data_access import (
    DataLayerError,
    DataRepository,
    build_repository,
)
from ml.devfixture import SyntheticDevRepository
from ml.schema import ParticipantHistory, ParticipantSummary

REQUIRED_METHODS = (
    "list_participants",
    "get_participant",
    "get_history",
    "get_meals",
    "export_model_examples",
)


@pytest.fixture(scope="module")
def candidate() -> DataRepository:
    """The repository under test. Swap for Het's implementation when it lands."""
    return SyntheticDevRepository()


class TestProtocolSurface:
    def test_satisfies_the_protocol(self, candidate):
        assert isinstance(candidate, DataRepository)

    @pytest.mark.parametrize("method", REQUIRED_METHODS)
    def test_declares_every_required_method(self, candidate, method):
        assert callable(getattr(candidate, method, None)), f"missing {method}()"


class TestParticipantRoster:
    def test_returns_typed_summaries(self, candidate):
        summaries = candidate.list_participants()
        assert summaries
        for summary in summaries:
            assert isinstance(summary, ParticipantSummary)

    def test_sorted_by_participant_id(self, candidate):
        ids = [s.participant_id for s in candidate.list_participants()]
        assert ids == sorted(ids)

    def test_every_record_is_provenance_labelled(self, candidate):
        for summary in candidate.list_participants():
            assert summary.data_source in ("synthetic_dev", "official", "mixed", "unknown")

    def test_days_available_is_non_negative(self, candidate):
        for summary in candidate.list_participants():
            assert summary.days_available >= 0

    def test_get_participant_round_trips(self, candidate):
        first = candidate.list_participants()[0]
        assert candidate.get_participant(first.participant_id) == first

    def test_unknown_participant_returns_none_rather_than_raising(self, candidate):
        """The backend converts `None` into 404; an exception would become a 500."""
        assert candidate.get_participant("definitely_not_a_participant") is None


class TestHistory:
    def test_returns_a_typed_history(self, candidate):
        history = candidate.get_history("synthetic_p001")
        assert isinstance(history, ParticipantHistory)

    def test_every_modality_is_chronologically_sorted(self, candidate):
        history = candidate.get_history("synthetic_p001")
        for records in (
            history.cgm,
            history.meals,
            history.heart_rate,
            history.medications,
        ):
            timestamps = [record.timestamp for record in records]
            assert timestamps == sorted(timestamps)

    def test_all_timestamps_are_offset_aware_utc(self, candidate):
        history = candidate.get_history("synthetic_p001")
        for record in [*history.cgm, *history.meals, *history.heart_rate]:
            assert record.timestamp.tzinfo is not None
            assert record.timestamp.utcoffset() == dt.timedelta(0)

    def test_strict_participant_filtering(self, candidate):
        """Cross-participant contamination is a P0 defect."""
        for participant_id in ("synthetic_p001", "synthetic_p003"):
            history = candidate.get_history(participant_id)
            assert history.participant_id == participant_id
            for meal in history.meals:
                assert meal.participant_id == participant_id

    def test_absent_optional_modality_is_an_empty_list(self, candidate):
        history = candidate.get_history("synthetic_p005")
        assert history.heart_rate == []
        assert history.heart_rate is not None

    def test_unknown_participant_yields_an_empty_history(self, candidate):
        history = candidate.get_history("definitely_not_a_participant")
        assert history.cgm == []
        assert history.meals == []

    def test_inclusive_window_filtering(self, candidate):
        full = candidate.get_history("synthetic_p001")
        start = full.cgm[10].timestamp
        end = full.cgm[20].timestamp
        windowed = candidate.get_history("synthetic_p001", start=start, end=end)
        assert windowed.cgm[0].timestamp == start
        assert windowed.cgm[-1].timestamp == end
        assert len(windowed.cgm) == 11

    def test_none_bounds_mean_unbounded(self, candidate):
        assert len(candidate.get_history("synthetic_p001", None, None).cgm) == len(
            candidate.get_history("synthetic_p001").cgm
        )

    def test_repeated_calls_are_identical(self, candidate):
        """Deterministic ordering, so chronological splits are stable."""
        first = candidate.get_history("synthetic_p002")
        second = candidate.get_history("synthetic_p002")
        assert [p.timestamp for p in first.cgm] == [p.timestamp for p in second.cgm]
        assert [m.meal_id for m in first.meals] == [m.meal_id for m in second.meals]


class TestMealsAndExport:
    def test_get_meals_matches_the_history_meals(self, candidate):
        assert candidate.get_meals("synthetic_p001") == candidate.get_history(
            "synthetic_p001"
        ).meals

    def test_export_spans_every_participant(self, candidate):
        exported = candidate.export_model_examples()
        participants = {meal.participant_id for meal in exported}
        assert participants == {
            summary.participant_id for summary in candidate.list_participants()
        }

    def test_export_is_deterministically_ordered(self, candidate):
        exported = candidate.export_model_examples()
        keys = [(meal.timestamp, meal.meal_id) for meal in exported]
        assert keys == sorted(keys)

    def test_export_cutoff_is_inclusive_and_truncates(self, candidate):
        everything = candidate.export_model_examples()
        cutoff = everything[len(everything) // 2].timestamp
        truncated = candidate.export_model_examples(cutoff=cutoff)
        assert truncated
        assert len(truncated) < len(everything)
        for meal in truncated:
            assert meal.timestamp <= cutoff


class TestBackendResolution:
    def test_default_backend_is_the_labelled_dev_fixture(self):
        repository = build_repository(load_settings())
        assert isinstance(repository, DataRepository)
        for summary in repository.list_participants():
            assert summary.data_source == "synthetic_dev"

    def test_unknown_backend_raises_a_clear_error(self):
        settings = Settings(data_backend="postgres_somewhere")
        with pytest.raises(DataLayerError, match="unknown GLUCOTWIN_DATA_BACKEND"):
            build_repository(settings)

    # Replaces `test_sqlite_backend_fails_informatively_until_the_data_layer_lands`, which
    # asserted the data layer was absent; it has landed (approved replacement).
    def test_sqlite_backend_binds_the_canonical_repository(self, tmp_path):
        from data.adapters import import_fixture
        from data.storage.db import init_db, make_engine

        url = f"sqlite:///{tmp_path / 'canonical.db'}"
        engine = make_engine(url)
        init_db(engine)
        import_fixture(engine=engine, report_dir=tmp_path / "reports")
        repository = build_repository(Settings(data_backend="sqlite", db_url=url))
        assert isinstance(repository, DataRepository)
        assert type(repository).__name__ == "SqliteDataRepository"
        assert [s.participant_id for s in repository.list_participants()][:1] == ["synthetic_p001"]

    def test_sqlite_backend_fails_informatively_without_a_database(self, tmp_path):
        """An uninitialized database must say what to run, not fail opaquely."""
        settings = Settings(data_backend="sqlite", db_url=f"sqlite:///{tmp_path / 'missing.db'}")
        with pytest.raises(RuntimeError, match="not initialized"):
            build_repository(settings)
