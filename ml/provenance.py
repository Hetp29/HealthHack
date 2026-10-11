"""Provenance resolution: where observations came from, and what their clock means.

Two questions the ML layer must answer from the data itself rather than from a CLI
flag, because getting either wrong silently mislabels a scientific result:

**What is the `data_source`?** (ruling D2) The frozen contract enum is
`synthetic_dev | official | mixed | unknown`. `sqlite` is a *storage backend*, not an
origin, so it must never reach the public field. The label is read off the participant
records, and a database holding more than one provenance is REFUSED rather than
silently flattened -- a mixed-source artifact whose metrics are reported as "official"
would be an unfalsifiable claim.

**What does a timestamp mean?** (ruling D3) The organizer datasets record offset-naive,
date-shifted local wall-clock times with no timezone. The data layer stores them with a
`+00:00` label so the contract's offset-aware requirement is met, and records
`participants.time_basis = "source_local_unknown_tz"` to say that the label is an
ordering key, NOT a real UTC instant. Time-of-day features must therefore read the
clock directly instead of converting. For genuinely UTC data with no known timezone the
local time of day is simply unknowable, and is reported missing rather than guessed.

Both resolvers duck-type the repository's optional extension methods
(`get_time_basis`), so they work against Het's `SqliteDataRepository`, against the
in-memory development fixture, and against any future implementation -- without the ML
layer depending on a concrete class.
"""

from __future__ import annotations

from ml.schema import DataSource

#: Values the frozen contract enum permits for `data_source`.
VALID_DATA_SOURCES: tuple[str, ...] = ("synthetic_dev", "official", "mixed", "unknown")

#: `time_basis` values the data layer records on each participant.
UTC_BASIS = "utc"
SOURCE_LOCAL_UNKNOWN_TZ = "source_local_unknown_tz"


class ProvenanceError(RuntimeError):
    """The dataset's provenance could not be established unambiguously."""


def resolve_data_source(repository) -> DataSource:
    """Determine the contract `data_source` label from the participant records.

    Raises `ProvenanceError` on an empty database or on mixed provenance. Refusing is
    deliberate: `mixed` exists in the enum, but using it automatically would let a
    half-synthetic, half-official database be trained on and reported without anyone
    deciding that was acceptable. Pooling sources is a modelling decision (ruling D5),
    not a labelling convenience.
    """
    summaries = repository.list_participants()
    if not summaries:
        raise ProvenanceError(
            "the repository has no participants, so its provenance cannot be "
            "established; refusing to label an artifact trained on nothing"
        )

    labels = sorted({str(summary.data_source) for summary in summaries})

    unknown = [label for label in labels if label not in VALID_DATA_SOURCES]
    if unknown:
        raise ProvenanceError(
            f"participant records carry data_source values {unknown} that are not in "
            f"the frozen contract enum {VALID_DATA_SOURCES}"
        )

    if len(labels) > 1:
        counts = {}
        for summary in summaries:
            counts[str(summary.data_source)] = counts.get(str(summary.data_source), 0) + 1
        raise ProvenanceError(
            f"the repository mixes provenances {counts}. Refusing to label this "
            "artifact automatically: pooling datasets is a modelling decision that "
            "needs an explicit evaluation (ruling D5), not a 'mixed' label. Train "
            "per source, or pass an explicit --data-source once pooling is justified."
        )

    return labels[0]  # type: ignore[return-value]


def resolve_time_basis(
    repository, participant_id: str, fallback_timezone: str | None = None
) -> tuple[str, str | None]:
    """Return `(time_basis, timezone_name)` for one participant.

    Repositories that do not implement the `get_time_basis` extension are treated as
    real-UTC with whatever timezone their `ParticipantSummary` carries, which is the
    correct reading for the in-memory development fixture.
    """
    getter = getattr(repository, "get_time_basis", None)
    if callable(getter):
        basis = getter(participant_id)
        if basis:
            return str(basis.get("time_basis") or UTC_BASIS), basis.get("timezone")
    return UTC_BASIS, fallback_timezone


def describe_time_basis(time_basis: str, timezone_name: str | None) -> str | None:
    """A caveat string for the API's `limitations`, or `None` when nothing is wrong.

    Replaces the previous wording, which claimed a UTC fallback was degrading the
    circadian features. For `source_local_unknown_tz` that was simply false: the stored
    clock reading IS the participant's local wall clock, so time of day is exact and the
    real limitation is elsewhere -- absolute instants are not comparable across
    participants.
    """
    if time_basis == SOURCE_LOCAL_UNKNOWN_TZ:
        return (
            "This dataset records offset-naive local wall-clock times with no stated "
            "timezone, and the dates are shifted. Time-of-day and within-participant "
            "elapsed time are exact, but the timestamps are ordering keys rather than "
            "real UTC instants, so they are not comparable across participants or with "
            "a real clock."
        )
    if time_basis == UTC_BASIS and not timezone_name:
        return (
            "No participant timezone is recorded for this UTC-based dataset, so the "
            "local time of day is unknown and the time-of-day features were left "
            "missing rather than guessed."
        )
    return None
