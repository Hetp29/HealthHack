"""The data-layer boundary.

================================= FOR HET ====================================
This file is the authoritative Python declaration of the interface the backend
consumes. Implement `DataRepository` in `data/` and the backend binds to it with
no change on the ML/backend side: add a branch to `build_repository` below (or
tell me and I will add it) and set `GLUCOTWIN_DATA_BACKEND=sqlite`.

It is a `typing.Protocol`, so there is nothing to subclass or import from
`backend/`. Any object with these five methods satisfies it, and
`tests/test_data_contract.py` checks a candidate against it structurally.

Contract on every method:
  * Deterministic ordering: ascending `timestamp`, then ascending `meal_id` as
    tie-breaker. Two calls with the same arguments return identical sequences.
  * `start` / `end` are inclusive when supplied; `None` means unbounded.
  * Offset-aware UTC datetimes only, in and out. Reject naive input.
  * `get_participant` returns `None` for an unknown id -- it does not raise. The
    backend turns that into `404 participant_not_found`.
  * An absent optional modality is an empty list, never `None`.
  * Strict participant filtering. A record belonging to another participant must
    never appear; cross-participant contamination is a P0 defect.

Record types come from `ml/schema.py`, which is the typed mirror of
`contracts/canonical_schema_v1.md`. Import them from there:

    from ml.schema import CgmPoint, MealRecord, ParticipantHistory, ...
==============================================================================
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from ml.schema import MealRecord, ParticipantHistory, ParticipantSummary


@runtime_checkable
class DataRepository(Protocol):
    """Read-only access to canonical participant records."""

    def list_participants(self) -> list[ParticipantSummary]:
        """Every participant, sorted by `participant_id`. May be empty."""
        ...

    def get_participant(self, participant_id: str) -> ParticipantSummary | None:
        """One participant, or `None` when the id is unknown."""
        ...

    def get_history(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> ParticipantHistory:
        """All observed records for one participant within the inclusive window."""
        ...

    def get_meals(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> list[MealRecord]:
        """Logged meals for one participant within the inclusive window."""
        ...

    def export_model_examples(
        self, cutoff: dt.datetime | None = None
    ) -> list[MealRecord]:
        """Every meal across all participants at or before `cutoff`, for ML export."""
        ...


class DataLayerError(RuntimeError):
    """The configured data backend could not be constructed or reached."""


def build_repository(settings) -> DataRepository:
    """Resolve `GLUCOTWIN_DATA_BACKEND` to a repository instance.

    `synthetic_dev` is the labelled temporary ML development fixture. It is the
    default only until the canonical data layer lands; every record it produces
    carries `data_source="synthetic_dev"` so the provenance is visible in the UI.
    """
    backend = settings.data_backend

    if backend == "synthetic_dev":
        from ml.devfixture import SyntheticDevRepository

        return SyntheticDevRepository(
            days=settings.fixture_days, seed=settings.fixture_seed
        )

    if backend == "sqlite":
        # The canonical data layer (owner: Het). Two distinct failures are possible and
        # BOTH must degrade rather than kill the process: the module may be missing, or
        # it may import fine but the database may not be initialized. Either way the
        # service keeps serving /health and /api/model/info so an operator can see why
        # the data layer is down, and /api/* returns 503 data_layer_unavailable.
        try:
            from data.storage.repository import SqliteDataRepository
        except Exception as error:
            raise DataLayerError(
                "GLUCOTWIN_DATA_BACKEND=sqlite requires the canonical data layer at "
                f"data/storage/repository.py, which is not importable: {error}"
            ) from error

        try:
            return SqliteDataRepository(settings.db_url)
        except Exception as error:
            # Typically DataLayerUnavailableError (schema absent). Deliberately broad:
            # a locked file, a bad URL or a permissions error must not crash startup
            # either.
            raise DataLayerError(
                f"could not open the canonical database at {settings.db_url}: {error}"
            ) from error

    raise DataLayerError(
        f"unknown GLUCOTWIN_DATA_BACKEND={backend!r}; expected 'synthetic_dev' or 'sqlite'"
    )
