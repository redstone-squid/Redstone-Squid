"""Every persisted timestamp column is stored and returned as a whenever `Instant`."""

from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine

import squid.persistence.model_registry  # noqa: F401  # imported for its model registration side effect
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


def _mapped_models() -> list[type[DeclarativeBase]]:
    """Every model the migration metadata knows about."""
    return [
        mapper.class_
        for mapper in Base.registry.mappers
        # Single-table inheritance maps several classes onto one table; the base carries
        # the columns, so mapping over every class would double-count them.
        if mapper.local_table is not None
    ]


def _stores_naive_datetime(column_type: TypeEngine[object]) -> bool:
    """Whether the column hands the application a stdlib `datetime` instead of an `Instant`."""
    try:
        return column_type.python_type is datetime
    except NotImplementedError:
        # Types like JSONB and enums do not declare a Python type; they are not timestamps.
        return False


def test_no_model_stores_a_bare_datetime() -> None:
    """Read off the registry rather than a hand-maintained list of columns.

    The list this replaces could only fail for columns someone had already remembered to
    add to it, which is precisely the case that was never going to regress. Sweeping the
    registry covers every column added from here on, on the day it lands.
    """
    naive = sorted(
        f"{model.__tablename__}.{column.name}"  # pyright: ignore[reportAttributeAccessIssue]
        for model in _mapped_models()
        for column in inspect(model).columns
        if _stores_naive_datetime(column.type)
    )

    assert naive == [], f"these columns bypass InstantUTC: {naive}"


def test_the_registry_sweep_actually_sees_timestamp_columns() -> None:
    """Guard the sweep above against passing vacuously.

    If model registration or the mapper walk silently produced nothing, the naive-column
    check would still pass. This fails instead.
    """
    instant_columns = {
        f"{model.__tablename__}.{column.name}"  # pyright: ignore[reportAttributeAccessIssue]
        for model in _mapped_models()
        for column in inspect(model).columns
        if isinstance(column.type, InstantUTC)
    }

    assert {"builds.submission_time", "vote_sessions.created_at"} <= instant_columns
