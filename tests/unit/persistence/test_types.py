"""Persistence type tests."""

from datetime import UTC, datetime, timedelta, timezone
from enum import IntEnum

from sqlalchemy import Column, MetaData, Table, create_engine, insert, select
from sqlalchemy.dialects import sqlite
from whenever import Instant

from squid.builds.domain import Status
from squid.builds.infrastructure.models import Build
from squid.persistence.types import InstantUTC, IntEnumSmallInt


class _Colour(IntEnum):
    RED = 0
    GREEN = 1


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


def test_int_enum_small_int_accepts_members_and_raw_integers() -> None:
    column_type = IntEnumSmallInt(_Colour)

    assert column_type.process_bind_param(_Colour.GREEN, sqlite.dialect()) == 1
    assert column_type.process_bind_param(1, sqlite.dialect()) == 1
    assert column_type.process_bind_param(None, sqlite.dialect()) is None


def test_int_enum_small_int_round_trips_as_a_member() -> None:
    metadata = MetaData()
    swatches = Table("swatches", metadata, Column("colour", IntEnumSmallInt(_Colour), nullable=False))
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(insert(swatches).values(colour=_Colour.GREEN))
        result = connection.scalar(select(swatches.c.colour))
    engine.dispose()

    # `is`, not `==`: an `IntEnum` read back as a bare `int` still compares equal.
    assert result is _Colour.GREEN


def test_build_submission_status_column_coerces_to_status() -> None:
    """`Mapped[Status]` over a bare `SmallInteger` handed every reader a plain `int`.

    Nothing that compared the value noticed, because `Status` is an `IntEnum`. What
    broke was `.name` in the search projection -- which dropped every build from the
    index -- and every `is Status.CONFIRMED` identity check, which silently became
    false for the entire catalogue.
    """
    column_type = Build.__table__.c.submission_status.type

    assert isinstance(column_type, IntEnumSmallInt)
    assert column_type.process_result_value(1, sqlite.dialect()) is Status.CONFIRMED
