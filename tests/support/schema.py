"""Build table subsets shared by repository tests without duplicating schemas."""

from sqlalchemy import Table

import squid.persistence.model_registry  # noqa: F401  # every model must be mapped before the walk
from squid.persistence.base import Base


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
