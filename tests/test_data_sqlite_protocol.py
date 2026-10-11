"""Ruhaan's data-contract acceptance suite, run against the real SQLite repository.

`tests/test_data_contract.py` defines the Protocol checks against his dev fixture and
says: "point `candidate` at your repository and this suite becomes an acceptance test".
The test classes are imported unchanged; only the `candidate` fixture differs (a
temporary database loaded with the committed synthetic_dev fixtures).
`TestBackendResolution` is not imported: it tests backend wiring, not the repository.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.app.services.data_access import DataRepository
from data.adapters import import_fixture
from data.storage.repository import DataLayerUnavailableError, SqliteDataRepository
from data.testing import temp_engine
from ml.schema import NaiveDatetimeError, ParticipantHistory
from tests.test_data_contract import (  # noqa: F401  (collected here with our candidate)
    TestHistory,
    TestMealsAndExport,
    TestParticipantRoster,
    TestProtocolSurface,
)

UTC = dt.timezone.utc


@pytest.fixture(scope="module")
def candidate(tmp_path_factory) -> DataRepository:
    tmp = tmp_path_factory.mktemp("sqlite_protocol")
    engine = temp_engine(tmp)
    import_fixture(engine=engine, report_dir=tmp / "reports")
    return SqliteDataRepository(engine=engine)


def test_backend_can_construct_it_from_a_url(tmp_path):
    engine = temp_engine(tmp_path, "x.db")
    import_fixture(engine=engine, report_dir=tmp_path / "reports")
    repo = SqliteDataRepository(f"sqlite:///{tmp_path / 'x.db'}")
    assert isinstance(repo, DataRepository) and len(repo.list_participants()) == 6


def test_uninitialized_database_raises_a_clear_error(tmp_path):
    with pytest.raises(DataLayerUnavailableError):
        SqliteDataRepository(f"sqlite:///{tmp_path / 'empty.db'}")


def test_history_carries_every_modality_and_provenance(candidate):
    h = candidate.get_history("synthetic_p002")
    assert h.data_source == "synthetic_dev"
    assert h.cgm and h.meals and h.heart_rate and h.medications and h.activity
    assert all(m.participant_id == "synthetic_p002" for m in h.medications)


def test_sparse_logger_and_summary_timezone(candidate):
    assert len(candidate.get_meals("synthetic_p006")) <= 3
    assert candidate.get_participant("synthetic_p001").timezone == "America/New_York"


def test_naive_bounds_are_rejected(candidate):
    with pytest.raises(NaiveDatetimeError):
        candidate.get_history("synthetic_p001", start=dt.datetime(2026, 10, 2))
    with pytest.raises(NaiveDatetimeError):
        candidate.export_model_examples(cutoff=dt.datetime(2026, 10, 2))


def test_as_of_history_never_contains_future_observations(candidate):
    meal = candidate.get_meals("synthetic_p003")[10]
    h = candidate.get_history_as_of("synthetic_p003", meal.timestamp)
    observed = [*h.cgm, *h.meals, *h.heart_rate, *h.medications, *h.activity]
    assert observed and max(x.timestamp for x in observed) <= meal.timestamp
    full = candidate.get_history("synthetic_p003")
    assert h == full.truncated_at(meal.timestamp)  # same answer as Ruhaan's chokepoint
    windowed = candidate.get_history_as_of("synthetic_p003", meal.timestamp, lookback=dt.timedelta(hours=2))
    assert all(meal.timestamp - dt.timedelta(hours=2) <= p.timestamp <= meal.timestamp for p in windowed.cgm)


def test_unknown_participant_is_empty_everywhere(candidate):
    h = candidate.get_history_as_of("nope", dt.datetime(2026, 10, 5, tzinfo=UTC))
    assert isinstance(h, ParticipantHistory) and h.cgm == [] and h.data_source == "unknown"
    assert candidate.get_meals("nope") == []
    assert candidate.get_participant_metadata("nope") is None
