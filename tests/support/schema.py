"""Build table subsets shared by repository tests without duplicating schemas."""

from sqlalchemy import Table

import squid.persistence.model_registry  # noqa: F401  # every model must be mapped before the walk
from squid.persistence.base import Base


def with_foreign_key_targets(*tables: Table) -> tuple[Table, ...]:
    """Return the given tables plus every table they reference, in creation order.

    Repository tests want to create the few tables their subject touches rather than the
    whole database, but writing out the transitive foreign-key targets by hand is how a
    subset turns into a second copy of the schema that drifts from the models. Deriving
    the closure keeps the test's list down to the tables it actually cares about.

    Resolving a foreign key needs its target mapped, which is why this module imports the
    registry: a caller that only imported its own models would otherwise fail here with a
    `NoReferencedTableError` for something two hops away.
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
