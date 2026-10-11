"""`SqliteDataRepository`: the canonical SQLite store behind the backend's Protocol.

This is the class `backend/app/services/data_access.py::build_repository` imports when
``GLUCOTWIN_DATA_BACKEND=sqlite``. It satisfies that ``DataRepository`` Protocol
structurally and returns the record types from ``ml/schema.py`` (the backend's typed
mirror of the canonical contract), so the ML/backend side binds with no changes.

Protocol rules honoured here (see data_access.py):
  * ascending ``timestamp`` (then ``meal_id``) ordering, deterministic across calls;
  * inclusive ``start``/``end``, ``None`` = unbounded; offset-aware UTC only;
  * ``get_participant`` -> ``None`` for an unknown id; ``get_history`` of an unknown
    id is an empty history (never an exception the API would turn into a 500);
  * an absent optional modality is ``[]``;
  * every query filters on exactly one ``participant_id``.

Beyond the Protocol, ``get_history_as_of(participant_id, as_of, lookback)`` filters in
SQL so that no observation later than ``as_of`` is even read: the prediction-time
leakage guard at the storage boundary.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Table, func, select
from sqlalchemy.engine import Engine

from data.storage import tables
from data.storage.db import is_initialized, make_engine
from data.timeutil import local_minutes_of_day
from ml.schema import (
    ActivityEvent,
    CgmPoint,
    HeartRatePoint,
    MealRecord,
    MedicationEvent,
    ParticipantHistory,
    ParticipantSummary,
    require_utc,
)


class DataLayerUnavailableError(RuntimeError):
    """Database missing or not initialized (the backend maps this to 503)."""


_MEAL_COLUMNS = ("meal_id", "participant_id", "timestamp", "carbs_g", "protein_g", "fat_g", "fiber_g",
                 "calories_kcal", "image_ref")


def _bound(value: dt.datetime | None, name: str) -> dt.datetime | None:
    return None if value is None else require_utc(value, name)


class SqliteDataRepository:
    """Read-only access to the canonical SQLite database."""

    def __init__(self, db_url: str | None = None, *, engine: Engine | None = None):
        self.engine = engine if engine is not None else make_engine(db_url)
        if not is_initialized(self.engine):
            raise DataLayerUnavailableError(
                f"canonical database at {self.engine.url} is not initialized; run "
                "`python -m data.seed` (synthetic_dev) or an official adapter first"
            )

    # ----------------------------------------------------------------- helpers

    def _select(self, table: Table, participant_id: str, start, end, order: tuple[str, ...]) -> list[dict]:
        stmt = select(table).where(table.c.participant_id == participant_id)
        if start is not None:
            stmt = stmt.where(table.c.timestamp >= start)
        if end is not None:
            stmt = stmt.where(table.c.timestamp <= end)
        stmt = stmt.order_by(*[table.c[c] for c in order])
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings()]

    def _participant_row(self, participant_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(tables.participants).where(tables.participants.c.participant_id == participant_id)
            ).mappings().first()
        return dict(row) if row else None

    def _summary(self, row: dict) -> ParticipantSummary:
        with self.engine.connect() as conn:
            first, last = conn.execute(
                select(func.min(tables.cgm.c.timestamp), func.max(tables.cgm.c.timestamp)).where(
                    tables.cgm.c.participant_id == row["participant_id"]
                )
            ).one()
        days = round((last - first).total_seconds() / 86400, 2) if first is not None else 0.0
        return ParticipantSummary(
            participant_id=row["participant_id"],
            display_name=row["display_name"],
            data_source=row["data_source"],
            days_available=days,
            timezone=row["timezone"],
        )

    @staticmethod
    def _meal(r: dict) -> MealRecord:
        return MealRecord(**{c: r[c] for c in _MEAL_COLUMNS})

    def _history(self, participant_id: str, start, end) -> ParticipantHistory:
        row = self._participant_row(participant_id)
        if row is None:
            return ParticipantHistory(participant_id=participant_id, data_source="unknown")
        return ParticipantHistory(
            participant_id=participant_id,
            data_source=row["data_source"],
            cgm=[CgmPoint(r["timestamp"], r["glucose_mg_dl"])
                 for r in self._select(tables.cgm, participant_id, start, end, ("timestamp",))],
            meals=[self._meal(r) for r in self._select(tables.meals, participant_id, start, end, ("timestamp", "meal_id"))],
            heart_rate=[HeartRatePoint(r["timestamp"], r["heart_rate_bpm"], r["resting_heart_rate_bpm"])
                        for r in self._select(tables.heart_rate, participant_id, start, end, ("timestamp",))],
            medications=[MedicationEvent(participant_id, r["timestamp"], r["medication_class"], r["dose"], r["dose_unit"])
                         for r in self._select(tables.medications, participant_id, start, end, ("timestamp", "id"))],
            activity=[ActivityEvent(participant_id, r["timestamp"], r["activity_value"], r["activity_unit"])
                      for r in self._select(tables.activity, participant_id, start, end, ("timestamp", "activity_unit"))],
        )

    # ----------------------------------------------------------------- Protocol

    def list_participants(self) -> list[ParticipantSummary]:
        p, c = tables.participants, tables.cgm
        # Correlated MIN/MAX per participant: two index seeks each, not a full CGM scan.
        first = select(func.min(c.c.timestamp)).where(c.c.participant_id == p.c.participant_id).scalar_subquery()
        last = select(func.max(c.c.timestamp)).where(c.c.participant_id == p.c.participant_id).scalar_subquery()
        stmt = select(
            p.c.participant_id, p.c.display_name, p.c.data_source, p.c.timezone,
            first.label("first"), last.label("last"),
        ).order_by(p.c.participant_id)
        out = []
        with self.engine.connect() as conn:
            for r in conn.execute(stmt).mappings():
                days = 0.0
                if r["first"] is not None:
                    days = round((r["last"] - r["first"]).total_seconds() / 86400, 2)
                out.append(ParticipantSummary(r["participant_id"], r["display_name"], r["data_source"], days, r["timezone"]))
        return out

    def get_participant(self, participant_id: str) -> ParticipantSummary | None:
        row = self._participant_row(participant_id)
        return None if row is None else self._summary(row)

    def get_history(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> ParticipantHistory:
        """Observed records for DISPLAY. Crosses meals; use get_history_as_of for features."""
        return self._history(participant_id, _bound(start, "start"), _bound(end, "end"))

    def get_meals(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> list[MealRecord]:
        rows = self._select(tables.meals, participant_id, _bound(start, "start"), _bound(end, "end"),
                            ("timestamp", "meal_id"))
        return [self._meal(r) for r in rows]

    def export_model_examples(self, cutoff: dt.datetime | None = None) -> list[MealRecord]:
        cutoff = _bound(cutoff, "cutoff")
        stmt = select(tables.meals).order_by(tables.meals.c.timestamp, tables.meals.c.meal_id)
        if cutoff is not None:
            stmt = stmt.where(tables.meals.c.timestamp <= cutoff)
        with self.engine.connect() as conn:
            return [self._meal(dict(r)) for r in conn.execute(stmt).mappings()]

    # ----------------------------------------------------------------- extensions

    def get_history_as_of(
        self,
        participant_id: str,
        as_of: dt.datetime,
        lookback: dt.timedelta | None = None,
    ) -> ParticipantHistory:
        """Everything observed at or before ``as_of`` (optionally only the last ``lookback``).

        Equivalent to ``get_history(...).truncated_at(as_of)`` but filtered in SQL, so a
        future observation is never read, not merely discarded.
        """
        if as_of is None:
            raise ValueError("as_of is required")
        as_of = require_utc(as_of, "as_of")
        start = as_of - lookback if lookback is not None else None
        return self._history(participant_id, start, as_of)

    def get_cgm_secondary(
        self, participant_id: str, start: dt.datetime | None = None, end: dt.datetime | None = None
    ) -> list[tuple[dt.datetime, str, float]]:
        """(timestamp, sensor, glucose_mg_dl) from additional simultaneously worn sensors."""
        rows = self._select(tables.cgm_secondary, participant_id, _bound(start, "start"), _bound(end, "end"),
                            ("timestamp", "sensor"))
        return [(r["timestamp"], r["sensor"], r["glucose_mg_dl"]) for r in rows]

    def get_time_basis(self, participant_id: str) -> dict | None:
        """``{"time_basis", "timezone"}``. ``source_local_unknown_tz`` means canonical
        timestamps are source wall clock with a +00:00 label, not real UTC instants."""
        row = self._participant_row(participant_id)
        return None if row is None else {"time_basis": row["time_basis"], "timezone": row["timezone"]}

    def local_minutes_of_day(self, participant_id: str, timestamp: dt.datetime) -> float | None:
        """Local time of day (minutes since midnight) for this participant, or None if unknowable."""
        basis = self.get_time_basis(participant_id)
        if basis is None:
            return None
        return local_minutes_of_day(require_utc(timestamp, "timestamp"), basis["time_basis"], basis["timezone"])

    def get_source_attributes(self, participant_id: str) -> list[dict]:
        """Preserved source fields with no canonical home (data layer only; not in the API)."""
        t = tables.source_attributes
        stmt = (select(t).where(t.c.participant_id == participant_id)
                .order_by(t.c.entity_type, t.c.entity_id, t.c.attribute))
        with self.engine.connect() as conn:
            return [{k: v for k, v in dict(r).items() if k != "ingestion_run_id"} for r in conn.execute(stmt).mappings()]

    def get_participant_metadata(self, participant_id: str) -> dict | None:
        """Full participant row including provenance (source_dataset, data_source, run)."""
        row = self._participant_row(participant_id)
        if row is None:
            return None
        with self.engine.connect() as conn:
            sensors = sorted(s for s in conn.execute(
                select(tables.cgm.c.sensor).where(tables.cgm.c.participant_id == participant_id).distinct()
            ).scalars() if s)
        return {**row, "cgm_sensors": sensors}
