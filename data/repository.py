"""Read-only, typed access to the canonical database: the one interface consumers use.

Implements the ``DataRepository`` interface in ``contracts/canonical_schema_v1.md``:

    list_participants() -> list[ParticipantSummary]
    get_participant(participant_id) -> ParticipantSummary | None
    get_history(participant_id, start=None, end=None) -> ParticipantHistory
    get_meals(participant_id, start=None, end=None) -> list[MealRecord]
    export_model_examples(cutoff=None) -> list[MealRecord]

plus per-modality readers and ``get_prediction_context(participant_id, as_of)``, which
returns only observations with ``timestamp <= as_of``.

Contract on every method: ascending ``timestamp`` order with a deterministic tie-breaker;
``None`` bounds are unbounded and supplied bounds are inclusive; absent modalities return
``[]``; datetimes in and out are offset-aware UTC (naive bounds raise ``ValueError``);
every query filters on exactly one ``participant_id``.

Unknown participant: ``get_participant`` returns ``None``. Per-participant readers
raise ``ParticipantNotFoundError`` (a ``LookupError``) so a typo can never look like
"participant with no data". (Awaiting confirmation against the backend Protocol in
``backend/app/services/data_access.py``, which is not committed yet.)
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Table, func, select
from sqlalchemy.engine import Engine

from data.quality import quality_summary
from data.schema import (
    ActivityRecord,
    CgmPoint,
    HeartRatePoint,
    IngestionRun,
    MealRecord,
    MedicationRecord,
    Participant,
    ParticipantHistory,
    ParticipantSummary,
    PredictionContext,
)
from data.storage import tables
from data.storage.db import init_db, is_initialized, make_engine
from data.timeutil import require_aware, to_rfc3339


class ParticipantNotFoundError(LookupError):
    def __init__(self, participant_id: str):
        super().__init__(f"unknown participant_id {participant_id!r}")
        self.participant_id = participant_id


class DataLayerUnavailableError(RuntimeError):
    """The database is missing or not initialized (backend maps this to 503)."""


_MEAL_FIELDS = ("meal_id", "participant_id", "timestamp", "carbs_g", "protein_g", "fat_g", "fiber_g",
                "calories_kcal", "image_ref")


class DataRepository:
    def __init__(self, db_url: str | None = None, *, engine: Engine | None = None):
        self.engine = engine if engine is not None else make_engine(db_url)
        if not is_initialized(self.engine):
            raise DataLayerUnavailableError(
                "database is not initialized; run `python -m data.seed` or "
                "`python -m data.adapters.ingest --source synthetic_dev`"
            )

    # ------------------------------------------------------------- helpers

    def _rows(self, table: Table, participant_id: str, start, end, order: tuple):
        start = require_aware(start, "start")
        end = require_aware(end, "end")
        stmt = select(table).where(table.c.participant_id == participant_id)
        if start is not None:
            stmt = stmt.where(table.c.timestamp >= start)
        if end is not None:
            stmt = stmt.where(table.c.timestamp <= end)
        stmt = stmt.order_by(*[table.c[name] for name in order])
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings()]

    def _require(self, participant_id: str) -> None:
        with self.engine.connect() as conn:
            found = conn.execute(
                select(tables.participants.c.participant_id).where(
                    tables.participants.c.participant_id == participant_id
                )
            ).first()
        if found is None:
            raise ParticipantNotFoundError(participant_id)

    def _summary(self, row: dict) -> ParticipantSummary:
        with self.engine.connect() as conn:
            first, last = conn.execute(
                select(func.min(tables.cgm.c.timestamp), func.max(tables.cgm.c.timestamp)).where(
                    tables.cgm.c.participant_id == row["participant_id"]
                )
            ).one()
        days = 0.0
        if first is not None:
            first_dt = tables.UTCDateTime().process_result_value(first, None) if isinstance(first, str) else first
            last_dt = tables.UTCDateTime().process_result_value(last, None) if isinstance(last, str) else last
            days = round((last_dt - first_dt).total_seconds() / 86400, 2)
        return ParticipantSummary(
            participant_id=row["participant_id"],
            display_name=row["display_name"],
            data_source=row["data_source"],
            days_available=days,
        )

    # ------------------------------------------------------------- participants

    def list_participants(self) -> list[ParticipantSummary]:
        """All participants, ordered by ``participant_id``.

        ``days_available`` = (last CGM - first CGM) / 86400, rounded to 2 decimals
        (calendar span, not gap-free coverage), per contracts/openapi.yaml.
        """
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                select(tables.participants).order_by(tables.participants.c.participant_id)
            ).mappings()]
        return [self._summary(r) for r in rows]

    def get_participant(self, participant_id: str) -> ParticipantSummary | None:
        record = self.get_participant_record(participant_id)
        if record is None:
            return None
        return self._summary(record.model_dump())

    def get_participant_record(self, participant_id: str) -> Participant | None:
        """Full canonical participant row (includes ``timezone``)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                select(tables.participants).where(tables.participants.c.participant_id == participant_id)
            ).mappings().first()
        if row is None:
            return None
        return Participant(
            participant_id=row["participant_id"],
            display_name=row["display_name"],
            timezone=row["timezone"],
            data_source=row["data_source"],
        )

    # ------------------------------------------------------------- modalities

    def get_cgm(self, participant_id: str, start: datetime | None = None, end: datetime | None = None) -> list[CgmPoint]:
        self._require(participant_id)
        rows = self._rows(tables.cgm, participant_id, start, end, ("timestamp",))
        return [CgmPoint(timestamp=r["timestamp"], glucose_mg_dl=r["glucose_mg_dl"]) for r in rows]

    def get_heart_rate(
        self, participant_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[HeartRatePoint]:
        self._require(participant_id)
        rows = self._rows(tables.heart_rate, participant_id, start, end, ("timestamp",))
        return [
            HeartRatePoint(
                timestamp=r["timestamp"],
                heart_rate_bpm=r["heart_rate_bpm"],
                resting_heart_rate_bpm=r["resting_heart_rate_bpm"],
            )
            for r in rows
        ]

    def get_meals(self, participant_id: str, start: datetime | None = None, end: datetime | None = None) -> list[MealRecord]:
        self._require(participant_id)
        rows = self._rows(tables.meals, participant_id, start, end, ("timestamp", "meal_id"))
        return [MealRecord(**{k: r[k] for k in _MEAL_FIELDS}) for r in rows]

    def get_medications(
        self, participant_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[MedicationRecord]:
        self._require(participant_id)
        rows = self._rows(tables.medications, participant_id, start, end, ("timestamp", "id"))
        return [
            MedicationRecord(**{k: r[k] for k in ("id", "participant_id", "timestamp", "medication_class", "dose",
                                                  "dose_unit", "data_source")})
            for r in rows
        ]

    def get_activity(
        self, participant_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ActivityRecord]:
        self._require(participant_id)
        rows = self._rows(tables.activity, participant_id, start, end, ("timestamp", "activity_unit"))
        return [
            ActivityRecord(**{k: r[k] for k in ("participant_id", "timestamp", "activity_value", "activity_unit",
                                                "data_source")})
            for r in rows
        ]

    # ------------------------------------------------------------- composite views

    def get_history(
        self, participant_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> ParticipantHistory:
        """Observed history for DISPLAY (``GET /api/participants/{id}/history``).

        Not for building prediction inputs: with ``end`` past a meal it includes the
        post-meal glucose. Use ``get_prediction_context`` for model inputs.
        """
        return ParticipantHistory(
            participant_id=participant_id,
            cgm=self.get_cgm(participant_id, start, end),
            meals=self.get_meals(participant_id, start, end),
            heart_rate=self.get_heart_rate(participant_id, start, end),
        )

    def get_prediction_context(
        self, participant_id: str, as_of: datetime, lookback: timedelta | None = None
    ) -> PredictionContext:
        """Everything observed for one participant with ``timestamp <= as_of``.

        Leakage guard: no record later than ``as_of`` can appear. ``lookback`` (e.g.
        ``timedelta(hours=24)``) limits how far back to read; ``None`` reads all history.
        """
        if as_of is None:
            raise ValueError("as_of is required")
        as_of = require_aware(as_of, "as_of")
        start = as_of - lookback if lookback is not None else None
        return PredictionContext(
            participant_id=participant_id,
            as_of=as_of,
            lookback_start=start,
            cgm=self.get_cgm(participant_id, start, as_of),
            heart_rate=self.get_heart_rate(participant_id, start, as_of),
            meals=self.get_meals(participant_id, start, as_of),
            medications=self.get_medications(participant_id, start, as_of),
            activity=self.get_activity(participant_id, start, as_of),
        )

    # ------------------------------------------------------------- ML export

    def export_model_examples(self, cutoff: datetime | None = None) -> list[MealRecord]:
        """Every stored meal with ``timestamp <= cutoff`` (all participants).

        Ordered by ascending ``timestamp`` then ``meal_id``. Returns observed meals only;
        target construction, inclusion rules and splits belong to the ML owner, who reads
        the surrounding observations through the per-participant methods.
        """
        cutoff = require_aware(cutoff, "cutoff")
        stmt = select(tables.meals)
        if cutoff is not None:
            stmt = stmt.where(tables.meals.c.timestamp <= cutoff)
        stmt = stmt.order_by(tables.meals.c.timestamp, tables.meals.c.meal_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [MealRecord(**{k: r[k] for k in _MEAL_FIELDS}) for r in rows]

    def export_tables(self, out_dir: str | Path, cutoff: datetime | None = None) -> dict:
        """Write canonical CSVs (all modalities, ``timestamp <= cutoff``) + a manifest.

        Columns are canonical names/units only. Returns the manifest.
        """
        cutoff = require_aware(cutoff, "cutoff")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        specs = {
            "participants": (tables.participants, ("participant_id",)),
            "cgm": (tables.cgm, ("participant_id", "timestamp")),
            "heart_rate": (tables.heart_rate, ("participant_id", "timestamp")),
            "meals": (tables.meals, ("participant_id", "timestamp", "meal_id")),
            "medications": (tables.medications, ("participant_id", "timestamp", "id")),
            "activity": (tables.activity, ("participant_id", "timestamp", "activity_unit")),
        }
        manifest = {"cutoff": to_rfc3339(cutoff) if cutoff else None, "files": {}, "runs": []}
        with self.engine.connect() as conn:
            for name, (table, order) in specs.items():
                columns = [c.name for c in table.columns if c.name != "ingestion_run_id"]
                stmt = select(*[table.c[c] for c in columns]).order_by(*[table.c[c] for c in order])
                if cutoff is not None and "timestamp" in table.c:
                    stmt = stmt.where(table.c.timestamp <= cutoff)
                rows = conn.execute(stmt).all()
                path = out / f"{name}.csv"
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle, lineterminator="\n")
                    writer.writerow(columns)
                    for row in rows:
                        writer.writerow(["" if v is None else to_rfc3339(v) if isinstance(v, datetime) else v for v in row])
                manifest["files"][path.name] = {"rows": len(rows)}
        manifest["runs"] = [r.model_dump(mode="json") for r in self.list_ingestion_runs()]
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    # ------------------------------------------------------------- provenance

    def list_ingestion_runs(self) -> list[IngestionRun]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(tables.ingestion_runs).order_by(tables.ingestion_runs.c.created_at, tables.ingestion_runs.c.run_id)
            ).mappings().all()
        return [IngestionRun(**dict(r)) for r in rows]

    def data_sources(self) -> list[str]:
        with self.engine.connect() as conn:
            return sorted(conn.execute(select(tables.participants.c.data_source).distinct()).scalars())

    def quality_report(self) -> dict:
        """Data-quality summary over everything currently stored."""
        with self.engine.connect() as conn:
            def fetch(table: Table) -> list[dict]:
                return [dict(r) for r in conn.execute(select(table)).mappings()]

            return quality_summary(
                fetch(tables.participants),
                fetch(tables.cgm),
                fetch(tables.heart_rate),
                fetch(tables.meals),
                fetch(tables.medications),
                fetch(tables.activity),
            )


def open_repository(db_url: str | None = None, *, create: bool = False) -> DataRepository:
    """Convenience constructor. ``create=True`` initializes an empty schema if needed."""
    engine = make_engine(db_url)
    if create:
        init_db(engine)
    return DataRepository(engine=engine)
