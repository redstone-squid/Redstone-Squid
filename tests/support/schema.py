"""Build table subsets shared by repository tests without duplicating schemas."""

from typing import cast

from sqlalchemy import Table
from sqlalchemy.orm import DeclarativeBase

import squid.persistence.model_registry  # noqa: F401  # every model must be mapped before the walk
from squid.persistence.base import Base


def model_table(model: type[DeclarativeBase]) -> Table:
    """Return the table a model is mapped to.

    A subclass in a joined-inheritance hierarchy returns its own table, not the base table.
    """
    table = model.__table__
    # Declarative types `__table__` as `FromClause`; every model in this project maps a `Table`.
    assert isinstance(table, Table), model
    # Pyrefly narrows to a parametrised `Table` it won't accept as the plain return type.
    return cast(Table, table)


def model_tables(*models: type[DeclarativeBase]) -> tuple[Table, ...]:
    """Return each model's table, in the given order."""
    return tuple(model_table(model) for model in models)


def with_foreign_key_targets(*tables: Table) -> tuple[Table, ...]:
    """Return the given tables plus every table they reference, in creation order.

    Walk foreign keys transitively and order the result with Base.metadata.sorted_tables.
    Inputs must belong to that metadata; tables from another metadata collection are omitted.
    The model registry import ensures indirect foreign-key targets are available to the walk.
    """
    required: set[Table] = set()
    pending = list(tables)
    while pending:
        table = pending.pop()
        if table in required:
            continue
        required.add(table)
        pending.extend(key.column.table for key in table.foreign_keys)
    # `sorted_tables` orders by dependency and breaks `use_alter` cycles, which the
    # builds/messages pair needs.
    return tuple(table for table in Base.metadata.sorted_tables if table in required)
