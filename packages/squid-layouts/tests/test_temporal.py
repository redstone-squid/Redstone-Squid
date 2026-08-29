"""Portable zoned date-time values."""

from datetime import UTC, datetime

import pytest

import squid_layouts as sl


def test_zoned_datetime_retains_instant_and_named_zone() -> None:
    value = sl.temporal.ZonedDateTime(datetime(2026, 8, 22, 14, 30, 5, 123456, tzinfo=UTC), "America/New_York")

    assert value.instant == datetime(2026, 8, 22, 14, 30, 5, 123456, tzinfo=UTC)
    assert value.local.isoformat() == "2026-08-22T10:30:05.123456-04:00"
    assert value.isoformat() == "2026-08-22 10:30:05.123456-04:00[America/New_York]"


def test_zoned_datetime_normalizes_aware_instant_to_utc() -> None:
    value = sl.temporal.ZonedDateTime(datetime.fromisoformat("2026-08-22T10:30:00-04:00"), "America/New_York")

    assert value.instant == datetime(2026, 8, 22, 14, 30, tzinfo=UTC)
    assert value.instant.tzinfo is UTC


def test_zoned_datetime_identity_includes_timezone() -> None:
    instant = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)

    assert sl.temporal.ZonedDateTime(instant, "America/New_York") != sl.temporal.ZonedDateTime(
        instant, "America/Toronto"
    )


def test_zoned_datetime_rejects_naive_instants_and_unknown_zones() -> None:
    with pytest.raises(ValueError, match="aware"):
        sl.temporal.ZonedDateTime(datetime(2026, 8, 22, 14, 30), "UTC")  # noqa: DTZ001 - exercises naive rejection
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        sl.temporal.ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "Mars/Olympus_Mons")
