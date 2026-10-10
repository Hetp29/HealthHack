"""SQLAlchemy table definitions (canonical schema v1.0).

Tables, keys and indexes follow the master spec "Data model and storage design":
participants, cgm, heart_rate, meals, medications, (optional) activity, ingestion_runs.
Two internal support tables are added: ``quarantine`` (rejected rows, required by the
"maintain rejection quarantine" rule) and ``schema_meta`` (stored schema version).

Every event row carries ``data_source`` and the ``ingestion_run_id`` that last wrote it.
Timestamps are stored as fixed-width UTC text (see ``timeutil.STORAGE_FORMAT``) so that
SQLite string ordering equals chronological ordering.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator

from data.timeutil import from_storage, to_storage

metadata = MetaData()

_DATA_SOURCE_CHECK = "data_source IN ('synthetic_dev','official','mixed','unknown')"


class UTCDateTime(TypeDecorator):
    """Aware datetime <-> fixed-width UTC string. Rejects naive datetimes on write."""

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        return None if value is None else to_storage(value)

    def process_result_value(self, value: str | None, dialect):
        return None if value is None else from_storage(value)


def _participant_fk(primary_key: bool = False) -> Column:
    return Column(
        "participant_id",
        String,
        ForeignKey("participants.participant_id", ondelete="RESTRICT"),
        nullable=False,
        primary_key=primary_key,
    )


def _provenance() -> list[Column]:
    return [
        Column("data_source", String, nullable=False),
        Column("ingestion_run_id", String, ForeignKey("ingestion_runs.run_id"), nullable=True),
    ]


schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("source", String, nullable=False),
    Column("data_source", String, nullable=False),
    Column("schema_version", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("records", Integer, nullable=False),
    Column("rejected", Integer, nullable=False),
    Column("warnings", Integer, nullable=False, default=0),
    Column("error_summary", Text, nullable=True),
    Column("sha256", String(64), nullable=False),
    Column("status", String, nullable=False),
    Column("mapping_name", String, nullable=True),
    Column("report_path", String, nullable=True),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_runs_data_source"),
)

participants = Table(
    "participants",
    metadata,
    Column("participant_id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("timezone", String, nullable=True),
    *_provenance(),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_participants_data_source"),
)

cgm = Table(
    "cgm",
    metadata,
    # Composite primary key (participant_id, timestamp): one reading per instant.
    _participant_fk(primary_key=True),
    Column("timestamp", UTCDateTime, primary_key=True),
    Column("glucose_mg_dl", Float, nullable=False),
    *_provenance(),
    CheckConstraint("glucose_mg_dl > 0", name="ck_cgm_positive"),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_cgm_data_source"),
)

heart_rate = Table(
    "heart_rate",
    metadata,
    # Composite primary key (participant_id, timestamp): one reading per instant.
    _participant_fk(primary_key=True),
    Column("timestamp", UTCDateTime, primary_key=True),
    Column("heart_rate_bpm", Float, nullable=False),
    Column("resting_heart_rate_bpm", Float, nullable=True),
    *_provenance(),
    CheckConstraint("heart_rate_bpm > 0", name="ck_hr_positive"),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_hr_data_source"),
)

meals = Table(
    "meals",
    metadata,
    Column("meal_id", String, primary_key=True),
    _participant_fk(),
    Column("timestamp", UTCDateTime, nullable=False),
    Column("carbs_g", Float, nullable=True),
    Column("protein_g", Float, nullable=True),
    Column("fat_g", Float, nullable=True),
    Column("fiber_g", Float, nullable=True),
    Column("calories_kcal", Float, nullable=True),
    Column("image_ref", String, nullable=True),
    *_provenance(),
    CheckConstraint(
        "COALESCE(carbs_g,0) >= 0 AND COALESCE(protein_g,0) >= 0 AND COALESCE(fat_g,0) >= 0"
        " AND COALESCE(fiber_g,0) >= 0 AND COALESCE(calories_kcal,0) >= 0",
        name="ck_meals_nonnegative",
    ),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_meals_data_source"),
)

medications = Table(
    "medications",
    metadata,
    Column("id", String, primary_key=True),
    _participant_fk(),
    Column("timestamp", UTCDateTime, nullable=False),
    Column("medication_class", String, nullable=False),
    Column("dose", Float, nullable=True),
    Column("dose_unit", String, nullable=True),
    *_provenance(),
    UniqueConstraint("participant_id", "timestamp", "medication_class", name="uq_medication_event"),
    CheckConstraint("dose IS NULL OR (dose >= 0 AND dose_unit IS NOT NULL)", name="ck_med_dose"),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_med_data_source"),
)

activity = Table(
    "activity",
    metadata,
    Column("id", String, primary_key=True),
    _participant_fk(),
    Column("timestamp", UTCDateTime, nullable=False),
    Column("activity_value", Float, nullable=True),
    Column("activity_unit", String, nullable=True),
    *_provenance(),
    UniqueConstraint("participant_id", "timestamp", "activity_unit", name="uq_activity_event"),
    CheckConstraint(_DATA_SOURCE_CHECK, name="ck_activity_data_source"),
)

quarantine = Table(
    "quarantine",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("ingestion_runs.run_id"), nullable=False),
    Column("modality", String, nullable=False),
    Column("source_file", String, nullable=True),
    Column("source_row", Integer, nullable=True),
    Column("participant_id", String, nullable=True),  # as supplied; may not exist
    Column("reasons", Text, nullable=False),  # comma-separated reason codes
    Column("raw_record", Text, nullable=False),  # JSON of the mapped source row
)

Index("ix_cgm_participant_time", cgm.c.participant_id, cgm.c.timestamp)
Index("ix_hr_participant_time", heart_rate.c.participant_id, heart_rate.c.timestamp)
Index("ix_meals_participant_time", meals.c.participant_id, meals.c.timestamp)
Index("ix_meds_participant_time", medications.c.participant_id, medications.c.timestamp)
Index("ix_activity_participant_time", activity.c.participant_id, activity.c.timestamp)
Index("ix_quarantine_run", quarantine.c.run_id)

EVENT_TABLES = {
    "cgm": cgm,
    "heart_rate": heart_rate,
    "meals": meals,
    "medications": medications,
    "activity": activity,
}
