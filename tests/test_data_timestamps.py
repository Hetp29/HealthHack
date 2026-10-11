from datetime import datetime, timedelta, timezone

import pytest

from data.timeutil import TimestampError, from_storage, parse_timestamp, require_aware, to_rfc3339, to_storage

UTC = timezone.utc


def test_z_and_offsets_normalize_to_utc():
    assert parse_timestamp("2026-10-08T12:30:00Z") == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    got = parse_timestamp("2026-10-08T08:30:00-04:00")
    assert got == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert got.utcoffset() == timedelta(0)
    assert parse_timestamp("2026-10-08T18:00:00+05:30") == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-10-08T12:30:00", "2026-10-08 12:30", "2026-10-08"])
def test_naive_timestamps_are_rejected_not_guessed(value):
    with pytest.raises(TimestampError) as exc:
        parse_timestamp(value)
    assert exc.value.code == "naive_timestamp"


def test_naive_with_explicit_timezone_is_localized():
    got = parse_timestamp("2026-10-08T08:30:00", assume_tz="America/New_York")
    assert got == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)


def test_dst_ambiguous_and_nonexistent_local_times_are_rejected():
    with pytest.raises(TimestampError) as exc:  # fall-back: 01:30 happens twice
        parse_timestamp("2026-11-01T01:30:00", assume_tz="America/New_York")
    assert exc.value.code == "ambiguous_local_time"
    with pytest.raises(TimestampError) as exc:  # spring-forward: 02:30 never happens
        parse_timestamp("2026-03-08T02:30:00", assume_tz="America/New_York")
    assert exc.value.code == "nonexistent_local_time"


@pytest.mark.parametrize("value,code", [("", "missing_timestamp"), (None, "missing_timestamp"),
                                        ("yesterday", "unparseable_timestamp"), ("2026-13-45T00:00:00Z", "unparseable_timestamp")])
def test_missing_and_garbage(value, code):
    with pytest.raises(TimestampError) as exc:
        parse_timestamp(value)
    assert exc.value.code == code


def test_epoch_and_custom_formats():
    assert parse_timestamp("1791462600", fmt="unix_s") == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert parse_timestamp("1791462600000", fmt="unix_ms") == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert parse_timestamp("08/10/2026 12:30", fmt="%d/%m/%Y %H:%M", assume_tz="UTC") == datetime(
        2026, 10, 8, 12, 30, tzinfo=UTC
    )


def test_storage_format_is_fixed_width_and_sorts_chronologically():
    times = [datetime(2026, 10, 8, 9, 5, tzinfo=UTC), datetime(2026, 10, 8, 10, 0, 0, 500, tzinfo=UTC),
             datetime(2026, 10, 1, 23, 59, tzinfo=UTC)]
    stored = [to_storage(t) for t in times]
    assert len({len(s) for s in stored}) == 1
    assert sorted(stored) == [to_storage(t) for t in sorted(times)]
    assert [from_storage(s) for s in stored] == times
    with pytest.raises(ValueError):
        to_storage(datetime(2026, 10, 8, 12, 30))


def test_rfc3339_output_and_aware_bounds():
    assert to_rfc3339(datetime(2026, 10, 8, 12, 30, tzinfo=UTC)) == "2026-10-08T12:30:00Z"
    with pytest.raises(ValueError):
        require_aware(datetime(2026, 10, 8, 12, 30), "start")
    assert require_aware(None, "start") is None
