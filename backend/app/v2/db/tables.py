"""Application database schema (SQLAlchemy Core).

This is the store for real users. It is deliberately separate from the research
warehouse in `data/storage` (CGMacros / MetaboNet, owner: Het): different lifecycle,
different sensitivity, and a different database URL (`GLUCOTWIN_APP_DB_URL`).

Conventions that hold for every table:

  * Primary keys are opaque prefixed strings minted by `backend.app.v2.ids`.
  * Every row that belongs to a person carries `user_id` with a real foreign key, and
    every query filters on it. Ownership is never inferred from a child id alone.
  * Timestamps are offset-aware UTC (`UTCDateTime`). A naive datetime is rejected on
    write rather than assumed to be UTC.
  * `observed_at` is when a measurement physically happened; `received_at` is when
    GLIMPSE stored it. They are separate columns and are never substituted.
  * Glucose is stored in mg/dL, with the source value and unit kept alongside.

Schema changes go through Alembic (`migrations/`). `metadata.create_all` is used only
by tests.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator):
    """Offset-aware UTC datetime on every backend.

    PostgreSQL stores `timestamptz`. SQLite has no timezone support and would hand
    back naive values, so they are written as naive UTC and re-labelled on read.
    Naive input is refused: guessing an offset is how window boundaries drift.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("refusing to store an offset-naive datetime")
        value = value.astimezone(dt.timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: dt.datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


def _user_fk(*, primary_key: bool = False, nullable: bool = False) -> Column:
    return Column(
        "user_id",
        String(40),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=nullable,
        index=not primary_key,
    )


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #

users = Table(
    "users",
    metadata,
    Column("user_id", String(40), primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("display_name", String(120), nullable=True),
    #: IANA zone. Required: local time of day is a model input, and guessing it is not
    #: acceptable.
    Column("timezone", String(64), nullable=False),
    Column("status", String(16), nullable=False, default="active"),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("status IN ('active','disabled')", name="status"),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("session_id", String(40), primary_key=True),
    _user_fk(),
    #: SHA-256 of the bearer token. The token itself is never stored.
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("revoked_at", UTCDateTime, nullable=True),
)

#: One model participant per user. A new user is NEVER bound to a research-dataset
#: participant: the id is freshly minted, so the artifact holds no calibration for it.
participant_bindings = Table(
    "participant_bindings",
    metadata,
    Column("participant_id", String(64), primary_key=True),
    Column("user_id", String(40), ForeignKey("users.user_id", ondelete="CASCADE"),
           nullable=False, unique=True),
    Column("binding_kind", String(24), nullable=False, default="app_user"),
    Column("model_id", String(64), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint("binding_kind IN ('app_user')", name="binding_kind"),
)

#: Append-only. The current state of a consent is its most recent row.
consent_records = Table(
    "consent_records",
    metadata,
    Column("consent_id", String(40), primary_key=True),
    _user_fk(),
    Column("consent_type", String(40), nullable=False),
    Column("state", String(16), nullable=False),
    Column("policy_version", String(32), nullable=False),
    Column("recorded_at", UTCDateTime, nullable=False),
    CheckConstraint(
        "consent_type IN ('necessary_processing','personalization_history')",
        name="consent_type",
    ),
    CheckConstraint("state IN ('granted','withdrawn')", name="state"),
    Index("ix_consent_user_type_time", "user_id", "consent_type", "recorded_at"),
)

onboarding_responses = Table(
    "onboarding_responses",
    metadata,
    _user_fk(primary_key=True),
    Column("answers", JSON, nullable=False),
    Column("completed", Boolean, nullable=False, default=False),
    #: Incremented on every write; used for optimistic concurrency (409 on mismatch).
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", UTCDateTime, nullable=False),
)

# --------------------------------------------------------------------------- #
# devices and observations
# --------------------------------------------------------------------------- #

device_connections = Table(
    "device_connections",
    metadata,
    Column("connection_id", String(40), primary_key=True),
    _user_fk(),
    Column("provider", String(32), nullable=False),
    Column("kind", String(16), nullable=False),
    #: `authorized` = OAuth completed, no data yet. `connected` is reserved for a
    #: connection that has actually delivered authentic observations.
    #: `demo_simulated` = a labelled sample source in a demo account: no provider was
    #: contacted, no credential exists, and its observations are generated. It is a
    #: status of its own so that neither `authorized` nor `connected` ever means that.
    Column("status", String(24), nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("external_user_id", String(128), nullable=True),
    #: Fernet ciphertext. Never returned by any endpoint.
    Column("access_token_enc", Text, nullable=True),
    Column("refresh_token_enc", Text, nullable=True),
    Column("token_expires_at", UTCDateTime, nullable=True),
    Column("sync_cursor", String(255), nullable=True),
    Column("last_sync_at", UTCDateTime, nullable=True),
    Column("last_success_at", UTCDateTime, nullable=True),
    Column("last_error_code", String(64), nullable=True),
    Column("authentic_data_received_at", UTCDateTime, nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    UniqueConstraint("user_id", "provider", name="uq_device_connections_user_provider"),
    CheckConstraint("kind IN ('cgm','wearable')", name="kind"),
    CheckConstraint(
        "status IN ('pending','authorized','connected','error','revoked','disconnected',"
        "'demo_simulated')",
        name="status",
    ),
)

oauth_states = Table(
    "oauth_states",
    metadata,
    #: SHA-256 of the `state` value handed to the provider.
    Column("state_hash", String(64), primary_key=True),
    _user_fk(),
    Column("provider", String(32), nullable=False),
    Column("code_verifier", String(128), nullable=True),
    Column("redirect_uri", String(512), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("used_at", UTCDateTime, nullable=True),
)

import_batches = Table(
    "import_batches",
    metadata,
    Column("batch_id", String(40), primary_key=True),
    _user_fk(),
    Column("connection_id", String(40),
           ForeignKey("device_connections.connection_id", ondelete="SET NULL"), nullable=True),
    Column("provider", String(32), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("source_format", String(40), nullable=False),
    #: What kind of data this is. `demo` and `research_dataset` are never presented as
    #: a live device feed.
    Column("data_class", String(24), nullable=False, default="user_device"),
    Column("source_sha256", String(64), nullable=True),
    Column("received_at", UTCDateTime, nullable=False),
    Column("counts", JSON, nullable=False),
    Column("rejections", JSON, nullable=False),
    CheckConstraint(
        "data_class IN ('user_device','demo','research_dataset')", name="data_class"
    ),
)

glucose_observations = Table(
    "glucose_observations",
    metadata,
    Column("observation_id", String(40), primary_key=True),
    _user_fk(),
    Column("provider", String(32), nullable=False),
    Column("device_id", String(128), nullable=False),
    Column("record_kind", String(16), nullable=False, default="cgm"),
    #: The provider's own immutable id, when it supplies one.
    Column("source_record_id", String(128), nullable=True),
    Column("observed_at", UTCDateTime, nullable=False),
    Column("received_at", UTCDateTime, nullable=False),
    Column("glucose_mg_dl", Float, nullable=False),
    Column("source_value", Float, nullable=False),
    Column("source_unit", String(8), nullable=False),
    Column("conversion", String(40), nullable=False),
    #: The timestamp text exactly as the source wrote it, and the zone used to
    #: localize it when it arrived without an offset.
    Column("source_timestamp", String(64), nullable=True),
    Column("source_timezone", String(64), nullable=True),
    Column("quality_flags", JSON, nullable=False),
    Column("data_class", String(24), nullable=False, default="user_device"),
    Column("batch_id", String(40),
           ForeignKey("import_batches.batch_id", ondelete="SET NULL"), nullable=True),
    Column("correction_count", Integer, nullable=False, default=0),
    UniqueConstraint(
        "user_id", "provider", "device_id", "record_kind", "observed_at",
        name="uq_glucose_observation_identity",
    ),
    CheckConstraint("record_kind IN ('cgm','scan','fingerstick')", name="record_kind"),
    CheckConstraint("source_unit IN ('mg/dL','mmol/L')", name="source_unit"),
    CheckConstraint("glucose_mg_dl >= 20 AND glucose_mg_dl <= 600", name="plausible"),
    Index("ix_glucose_user_time", "user_id", "observed_at"),
    Index("ix_glucose_user_source_id", "user_id", "provider", "source_record_id"),
)

#: Prior values of an observation the source later corrected. The live row is updated
#: in place; what it used to say is kept here, so nothing is overwritten untraceably.
glucose_corrections = Table(
    "glucose_corrections",
    metadata,
    Column("correction_id", String(40), primary_key=True),
    Column("observation_id", String(40),
           ForeignKey("glucose_observations.observation_id", ondelete="CASCADE"),
           nullable=False, index=True),
    _user_fk(),
    Column("previous_glucose_mg_dl", Float, nullable=False),
    Column("previous_source_value", Float, nullable=False),
    Column("previous_received_at", UTCDateTime, nullable=False),
    Column("corrected_at", UTCDateTime, nullable=False),
    Column("batch_id", String(40), nullable=True),
)

wearable_observations = Table(
    "wearable_observations",
    metadata,
    Column("observation_id", String(40), primary_key=True),
    _user_fk(),
    Column("provider", String(32), nullable=False),
    Column("source_record_id", String(160), nullable=False),
    Column("metric", String(40), nullable=False),
    Column("value", Float, nullable=True),
    Column("unit", String(24), nullable=False),
    Column("observed_at", UTCDateTime, nullable=False),
    Column("observed_end_at", UTCDateTime, nullable=True),
    Column("received_at", UTCDateTime, nullable=False),
    Column("detail", JSON, nullable=False),
    Column("data_class", String(24), nullable=False, default="user_device"),
    UniqueConstraint(
        "user_id", "provider", "metric", "source_record_id",
        name="uq_wearable_observation_identity",
    ),
    Index("ix_wearable_user_metric_time", "user_id", "metric", "observed_at"),
)

# --------------------------------------------------------------------------- #
# meals
# --------------------------------------------------------------------------- #

meals = Table(
    "meals",
    metadata,
    Column("meal_id", String(40), primary_key=True),
    _user_fk(),
    Column("occurred_at", UTCDateTime, nullable=False),
    Column("entry_method", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("note", String(500), nullable=True),
    Column("current_confirmation_id", String(40), nullable=True),
    #: What the user says actually happened to the meal after logging it.
    Column("consumption_status", String(16), nullable=False, default="as_logged"),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("entry_method IN ('photo','manual')", name="entry_method"),
    CheckConstraint(
        "status IN ('draft','analyzed','confirmed','voided')", name="status"
    ),
    CheckConstraint(
        "consumption_status IN ('as_logged','partial','not_eaten')",
        name="consumption_status",
    ),
    Index("ix_meals_user_time", "user_id", "occurred_at"),
)

meal_photos = Table(
    "meal_photos",
    metadata,
    Column("photo_id", String(40), primary_key=True),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False, unique=True),
    _user_fk(),
    Column("object_key", String(255), nullable=False),
    Column("content_type", String(40), nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("deleted_at", UTCDateTime, nullable=True),
)

#: What the vision model said. Immutable; never edited by a confirmation.
meal_estimates = Table(
    "meal_estimates",
    metadata,
    Column("estimate_id", String(40), primary_key=True),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False, index=True),
    _user_fk(),
    Column("provider", String(24), nullable=False),
    Column("ai_model_requested", String(80), nullable=False),
    Column("ai_model_reported", String(80), nullable=True),
    Column("prompt_version", String(24), nullable=False),
    Column("status", String(16), nullable=False),
    Column("carbs_g", Float, nullable=True),
    Column("protein_g", Float, nullable=True),
    Column("fat_g", Float, nullable=True),
    Column("fiber_g", Float, nullable=True),
    Column("calories_kcal", Float, nullable=True),
    Column("structured", JSON, nullable=False),
    Column("warnings", JSON, nullable=False),
    Column("usage", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint("status IN ('ok','not_food')", name="status"),
)

#: What the user confirmed. Immutable and revision-numbered: a correction is a new
#: row, so a prediction keeps pointing at the exact inputs it was made from.
meal_confirmations = Table(
    "meal_confirmations",
    metadata,
    Column("confirmation_id", String(40), primary_key=True),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False),
    _user_fk(),
    Column("revision", Integer, nullable=False),
    Column("carbs_g", Float, nullable=False),
    Column("protein_g", Float, nullable=False),
    Column("fat_g", Float, nullable=False),
    Column("fiber_g", Float, nullable=False),
    Column("calories_kcal", Float, nullable=False),
    Column("source", String(24), nullable=False),
    Column("based_on_estimate_id", String(40), nullable=True),
    Column("confirmed_at", UTCDateTime, nullable=False),
    UniqueConstraint("meal_id", "revision", name="uq_meal_confirmations_meal_revision"),
    CheckConstraint(
        "source IN ('manual','ai_accepted','ai_edited','ai_unconfirmed')", name="source"
    ),
    CheckConstraint(
        "carbs_g >= 0 AND protein_g >= 0 AND fat_g >= 0 AND fiber_g >= 0 "
        "AND calories_kcal >= 0",
        name="non_negative",
    ),
)

# --------------------------------------------------------------------------- #
# predictions and outcomes
# --------------------------------------------------------------------------- #

#: Exactly what the frozen model was given. Enough to re-run the forecast bit for bit.
feature_snapshots = Table(
    "feature_snapshots",
    metadata,
    Column("snapshot_id", String(40), primary_key=True),
    _user_fk(),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False),
    Column("as_of", UTCDateTime, nullable=False),
    Column("feature_set_version", String(16), nullable=False),
    Column("feature_names", JSON, nullable=False),
    #: Parallel to `feature_names`; a missing feature is `null`, never a filled value.
    Column("feature_values", JSON, nullable=False),
    Column("missing_features", JSON, nullable=False),
    #: zlib-compressed canonical JSON of the history handed to the engine.
    Column("history_blob", LargeBinary, nullable=False),
    Column("history_sha256", String(64), nullable=False),
    Column("context", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

predictions = Table(
    "predictions",
    metadata,
    Column("prediction_id", String(40), primary_key=True),
    _user_fk(),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False),
    Column("confirmation_id", String(40),
           ForeignKey("meal_confirmations.confirmation_id", ondelete="CASCADE"),
           nullable=False),
    Column("snapshot_id", String(40),
           ForeignKey("feature_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False),
    Column("participant_id", String(64), nullable=False),
    Column("model_id", String(64), nullable=False),
    Column("model_version", String(24), nullable=False),
    Column("feature_set_version", String(16), nullable=False),
    Column("artifact_sha256", String(64), nullable=False),
    Column("source_commit", String(64), nullable=True),
    Column("prediction_status", String(24), nullable=False),
    Column("prediction_kind", String(16), nullable=False),
    Column("inputs_confirmed", Boolean, nullable=False),
    Column("personalization_policy", String(40), nullable=False),
    Column("personalization", JSON, nullable=False),
    Column("baseline_glucose_mg_dl", Float, nullable=False),
    #: horizon -> delta before / offset applied by participant calibration.
    Column("cohort_deltas", JSON, nullable=False),
    Column("applied_offsets", JSON, nullable=False),
    #: The frozen `ForecastResponse` shape, stored verbatim.
    Column("forecast", JSON, nullable=False),
    Column("request_id", String(40), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    #: One forecast per confirmed input set. Re-requesting returns the stored one.
    UniqueConstraint("meal_id", "confirmation_id", name="uq_predictions_meal_confirmation"),
    CheckConstraint(
        "prediction_kind IN ('prospective','retrospective')", name="prediction_kind"
    ),
    Index("ix_predictions_user_time", "user_id", "created_at"),
)

response_episodes = Table(
    "response_episodes",
    metadata,
    Column("episode_id", String(40), primary_key=True),
    Column("prediction_id", String(40),
           ForeignKey("predictions.prediction_id", ondelete="CASCADE"),
           nullable=False, unique=True),
    Column("meal_id", String(40), ForeignKey("meals.meal_id", ondelete="CASCADE"),
           nullable=False, index=True),
    _user_fk(),
    Column("window_end_at", UTCDateTime, nullable=False),
    #: Earliest instant scoring may run: window end plus the sensor-arrival grace.
    Column("eligible_after", UTCDateTime, nullable=False),
    Column("status", String(24), nullable=False),
    Column("eligible_for_personalization", Boolean, nullable=False, default=False),
    Column("ineligibility_reasons", JSON, nullable=False),
    Column("coverage", JSON, nullable=False),
    Column("observed", JSON, nullable=False),
    Column("errors", JSON, nullable=False),
    Column("scored_at", UTCDateTime, nullable=True),
    Column("score_revision", Integer, nullable=False, default=0),
    CheckConstraint(
        "status IN ('pending','scored','insufficient_data','voided')", name="status"
    ),
    Index("ix_episodes_user_status", "user_id", "status"),
)

# --------------------------------------------------------------------------- #
# medications
# --------------------------------------------------------------------------- #

medication_regimens = Table(
    "medication_regimens",
    metadata,
    Column("regimen_id", String(40), primary_key=True),
    _user_fk(),
    Column("name", String(160), nullable=False),
    Column("medication_class", String(40), nullable=True),
    Column("form", String(40), nullable=True),
    Column("dose_amount", Float, nullable=True),
    Column("dose_unit", String(24), nullable=True),
    Column("frequency", String(80), nullable=True),
    #: Local wall-clock "HH:MM" strings, interpreted in `timezone`.
    Column("schedule_times", JSON, nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("start_date", String(10), nullable=True),
    Column("end_date", String(10), nullable=True),
    Column("status", String(16), nullable=False, default="current"),
    Column("reminders_enabled", Boolean, nullable=False, default=False),
    Column("prescriber_note", String(500), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("status IN ('current','discontinued')", name="status"),
)

#: A dose EVENT. Deliberately a different table from the regimen: a prescription is
#: not evidence that anything was taken, and neither is a reminder.
medication_doses = Table(
    "medication_doses",
    metadata,
    Column("dose_id", String(40), primary_key=True),
    Column("regimen_id", String(40),
           ForeignKey("medication_regimens.regimen_id", ondelete="CASCADE"),
           nullable=False, index=True),
    _user_fk(),
    Column("scheduled_at", UTCDateTime, nullable=True),
    Column("action", String(16), nullable=False),
    #: Set only by an explicit `taken` confirmation from the user.
    Column("actual_at", UTCDateTime, nullable=True),
    Column("snoozed_until", UTCDateTime, nullable=True),
    Column("actor", String(16), nullable=False),
    Column("provenance", String(24), nullable=False),
    Column("client_event_id", String(80), nullable=True),
    Column("recorded_at", UTCDateTime, nullable=False),
    UniqueConstraint("user_id", "client_event_id", name="uq_medication_doses_client_event"),
    UniqueConstraint("regimen_id", "scheduled_at", name="uq_medication_doses_slot"),
    CheckConstraint(
        "action IN ('scheduled','taken','skipped','snoozed','unknown')", name="action"
    ),
    CheckConstraint("actor IN ('user','system')", name="actor"),
    CheckConstraint(
        "(action = 'taken') OR (actual_at IS NULL)", name="actual_only_when_taken"
    ),
)

# --------------------------------------------------------------------------- #
# alerts and notifications
# --------------------------------------------------------------------------- #

alert_rules = Table(
    "alert_rules",
    metadata,
    _user_fk(primary_key=True),
    Column("enabled", Boolean, nullable=False, default=False),
    Column("threshold_mg_dl", Float, nullable=False),
    Column("threshold_source_value", Float, nullable=False),
    Column("threshold_source_unit", String(8), nullable=False),
    Column("channels", JSON, nullable=False),
    Column("quiet_hours_start", String(5), nullable=True),
    Column("quiet_hours_end", String(5), nullable=True),
    Column("cooldown_minutes", Integer, nullable=False, default=60),
    Column("updated_at", UTCDateTime, nullable=False),
)

alert_events = Table(
    "alert_events",
    metadata,
    Column("alert_id", String(40), primary_key=True),
    _user_fk(),
    Column("prediction_id", String(40),
           ForeignKey("predictions.prediction_id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("acknowledged_at", UTCDateTime, nullable=True),
    UniqueConstraint("prediction_id", "kind", name="uq_alert_events_prediction_kind"),
    Index("ix_alerts_user_time", "user_id", "created_at"),
)

notification_deliveries = Table(
    "notification_deliveries",
    metadata,
    Column("delivery_id", String(40), primary_key=True),
    _user_fk(),
    Column("source_type", String(24), nullable=False),
    Column("source_id", String(40), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("status", String(24), nullable=False),
    Column("detail", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint(
        "source_type", "source_id", "channel", name="uq_notification_source_channel"
    ),
    Index("ix_notifications_user_time", "user_id", "created_at"),
)

# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #

#: Who did what to which record. `detail` carries ids, counts and state names only --
#: never a glucose value, a macro, a medication name or free text.
audit_events = Table(
    "audit_events",
    metadata,
    Column("audit_id", String(40), primary_key=True),
    #: Not a foreign key: the record of an account deletion must outlive the account.
    Column("user_id", String(40), nullable=True, index=True),
    Column("actor", String(24), nullable=False),
    Column("action", String(64), nullable=False),
    Column("entity_type", String(40), nullable=True),
    Column("entity_id", String(64), nullable=True),
    Column("detail", JSON, nullable=False),
    Column("request_id", String(40), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
)

# --------------------------------------------------------------------------- #
# demonstration replay
# --------------------------------------------------------------------------- #

#: One row per DEMO account that is replaying a historical research recording. Its
#: presence is what makes an account a demo account: for that account alone, "now"
#: is `position` instead of the wall clock. Observations later than `position` are
#: not in this database at all -- they are released from the source bundle only as
#: the replay advances.
demo_replay_state = Table(
    "demo_replay_state",
    metadata,
    _user_fk(primary_key=True),
    Column("scenario_id", String(64), nullable=False),
    Column("bundle_sha256", String(64), nullable=False),
    #: The simulated instant, on the recording's own (historical) timeline.
    Column("position", UTCDateTime, nullable=False),
    #: How many timeline events have been executed.
    Column("event_index", Integer, nullable=False, default=0),
    Column("status", String(16), nullable=False, default="ready"),
    #: What each executed event did: ids of the records it created. No health values.
    Column("log", JSON, nullable=False),
    #: Wall-clock bookkeeping, deliberately separate from `position`.
    Column("initialized_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("status IN ('ready','complete')", name="status"),
)
