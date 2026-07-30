"""Persistence type tests."""

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import Column, MetaData, Table, create_engine, insert, select
from sqlalchemy.dialects import sqlite
from whenever import Instant

from squid.persistence.types import InstantUTC


def test_instant_utc_binds_as_utc_datetime() -> None:
    value = Instant.from_utc(2026, 7, 30, hour=12, minute=34)

    bound = InstantUTC().process_bind_param(value, sqlite.dialect())

    assert bound == datetime(2026, 7, 30, 12, 34, tzinfo=UTC)


def test_instant_utc_normalizes_aware_results() -> None:
    value = datetime(2026, 7, 30, 20, 34, tzinfo=timezone(timedelta(hours=8)))

    result = InstantUTC().process_result_value(value, sqlite.dialect())

    assert result == Instant.from_utc(2026, 7, 30, hour=12, minute=34)


def test_instant_utc_treats_naive_driver_results_as_utc() -> None:
    value = datetime(2026, 7, 30, 12, 34)  # noqa: DTZ001 - database drivers may return naive values

    result = InstantUTC().process_result_value(value, sqlite.dialect())

    assert result == Instant.from_utc(2026, 7, 30, hour=12, minute=34)


def test_instant_utc_round_trips_through_sqlite() -> None:
    metadata = MetaData()
    events = Table("events", metadata, Column("occurred_at", InstantUTC(), nullable=False))
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    value = Instant.from_utc(2026, 7, 30, hour=12, minute=34)

    with engine.begin() as connection:
        connection.execute(insert(events).values(occurred_at=value))
        result = connection.scalar(select(events.c.occurred_at))
    engine.dispose()

    assert result == value
