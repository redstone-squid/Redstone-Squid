"""Check timestamp type declarations across registered models without querying a database."""

from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine

import squid.persistence.model_registry  # noqa: F401  # imported for its model registration side effect
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


def _mapped_models() -> list[type[DeclarativeBase]]:
    """Return registered model classes with a local table."""
    return [
        mapper.class_
        for mapper in Base.registry.mappers
        # Include every mapper with a table; inherited columns may appear more than once.
        if mapper.local_table is not None  # pyright: ignore[reportUnnecessaryComparison]
    ]


def _stores_naive_datetime(column_type: TypeEngine[object]) -> bool:
    """Whether the declared result type is a stdlib datetime, regardless of timezone support."""
    try:
        return column_type.python_type is datetime
    except NotImplementedError:
        # Ignore types that do not expose a Python result type.
        return False


def test_no_model_stores_a_bare_datetime() -> None:
    """Inspect registered columns so newly mapped timestamps are included automatically."""
    naive = sorted(
        f"{model.__tablename__}.{column.name}"  # pyright: ignore[reportAttributeAccessIssue]
        for model in _mapped_models()
        for column in inspect(model).columns
        if _stores_naive_datetime(column.type)
    )

    assert naive == [], f"these columns bypass InstantUTC: {naive}"


def test_the_registry_sweep_actually_sees_timestamp_columns() -> None:
    """Require known timestamp columns so an empty registry cannot make the check pass."""
    instant_columns = {
        f"{model.__tablename__}.{column.name}"  # pyright: ignore[reportAttributeAccessIssue]
        for model in _mapped_models()
        for column in inspect(model).columns
        if isinstance(column.type, InstantUTC)
    }

    assert {"builds.submission_time", "vote_sessions.created_at"} <= instant_columns
