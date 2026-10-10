"""The data-layer boundary -- acceptance suite for EVERY repository implementation.

Checks the `DataRepository` Protocol that `backend/app/services/data_access.py`
declares. Every test runs twice, against both implementations:

  * `memory` -- `ml/devfixture.py::SyntheticDevRepository`, the temporary in-memory
    fixture that let the ML pipeline be built before the data layer existed;
  * `sqlite` -- `data/storage/repository.py::SqliteDataRepository` (owner: Het), the
    canonical store, seeded here from the committed `data/fixtures/synthetic_dev`
    CSVs through Het's real ingestion adapter.

Running one suite against both is the point: it is what proves the backend can be
switched between them with a single environment variable and behave identically. The
two implementations share no code -- one is a dict of dataclasses, the other is SQL --
so agreement here is meaningful rather than tautological.

Both fixtures deliberately preserve the same edge cases: `synthetic_p005` has no
heart-rate data at all, and `synthetic_p006` is a sparse logger with too few meals to
personalize.
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


@pytest.fixture(scope="session")
def seeded_sqlite_url(tmp_path_factory) -> str:
    """Build a real canonical SQLite database from the committed fixtures.

    Uses Het's actual ingestion path (`data.seed` -> `import_fixture`) rather than
    inserting rows directly, so this exercises the adapter, the validators and the
    upsert logic, not just the read API.
    """
    from data.adapters import import_fixture
    from data.storage.db import init_db, make_engine

    db_path = tmp_path_factory.mktemp("canonical") / "glucotwin_test.db"
    url = f"sqlite:///{db_path}"
    engine = make_engine(url)
    init_db(engine)
    result = import_fixture(engine=engine)
    assert getattr(result, "status", "completed") == "completed", result
    return url


@pytest.fixture(scope="session", params=["memory", "sqlite"])
def candidate(request, seeded_sqlite_url) -> DataRepository:
    """The repository under test, once per implementation."""
    if request.param == "memory":
        return SyntheticDevRepository()
    from data.storage.repository import SqliteDataRepository

    return SqliteDataRepository(seeded_sqlite_url)


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

    # --- approved replacement for the obsolete negative test, from Het (8e436e5) ---
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

    # --- additional integration coverage (Ruhaan) ---
    def test_switching_backends_is_the_only_change_needed(self, seeded_sqlite_url):
        """The two implementations must agree on the roster the API will serve."""
        memory = build_repository(Settings(data_backend="synthetic_dev"))
        sqlite = build_repository(
            Settings(data_backend="sqlite", db_url=seeded_sqlite_url)
        )
        assert [p.participant_id for p in memory.list_participants()] == [
            p.participant_id for p in sqlite.list_participants()
        ]

    def test_uninitialized_database_degrades_instead_of_crashing(self, tmp_path):
        """Regression test for a real integration bug found while merging 0a72229.

        `SqliteDataRepository.__init__` raises when the schema is absent. That
        exception was not wrapped, so it escaped `build_repository`, escaped
        `PredictionService._load_repository` (which only caught `DataLayerError`), and
        killed the process at startup -- turning a recoverable "you have not seeded the
        database yet" into a dead deployment with no endpoint left to explain why.
        """
        settings = Settings(
            data_backend="sqlite", db_url=f"sqlite:///{tmp_path}/never_created.db"
        )
        with pytest.raises(DataLayerError) as excinfo:
            build_repository(settings)
        message = str(excinfo.value)
        assert "could not open the canonical database" in message
        assert "never_created.db" in message

    def test_service_survives_an_uninitialized_database(self, tmp_path):
        """End to end: liveness and readiness stay up, data endpoints return 503."""
        import dataclasses

        from fastapi.testclient import TestClient

        from backend.app.main import create_app

        settings = dataclasses.replace(
            load_settings(),
            data_backend="sqlite",
            db_url=f"sqlite:///{tmp_path}/never_created.db",
        )
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/model/info").status_code == 200
            response = client.get("/api/participants")
            assert response.status_code == 503
            assert response.json()["code"] == "data_layer_unavailable"
