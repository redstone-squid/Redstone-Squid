"""Replaceable PostgreSQL functions and triggers managed by Alembic."""

import re
from functools import cache
from pathlib import Path

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

ENTITY_SQL_PATH = Path(__file__).with_name("postgres_entities.sql")
"""Sole definition of the entities Alembic owns in the public schema."""

EXPECTED_FUNCTIONS = 13
EXPECTED_TRIGGERS = 42


def parse_entities(sql: str) -> list[ReplaceableEntity]:
    """Split `sql` into the functions and triggers Alembic owns.

    Takes the SQL rather than reading it so the counts below, and the statement patterns they
    guard, can be exercised against inputs the shipped file is never allowed to contain.

    The counts are asserted rather than trusted: a statement the patterns fail to match is
    dropped silently, and a short list would let a migration believe it had replaced an entity
    that is in fact still running its previous definition.
    """
    functions = re.findall(r"^CREATE FUNCTION .*?\$\$;", sql, flags=re.MULTILINE | re.DOTALL)
    triggers = re.findall(r"^CREATE (?:CONSTRAINT )?TRIGGER .*?;$", sql, flags=re.MULTILINE)
    if len(functions) != EXPECTED_FUNCTIONS or len(triggers) != EXPECTED_TRIGGERS:
        msg = (
            f"postgres_entities.sql must define exactly {EXPECTED_FUNCTIONS} functions and "
            f"{EXPECTED_TRIGGERS} triggers; parsed {len(functions)} and {len(triggers)}"
        )
        raise RuntimeError(msg)
    return [
        *(PGFunction.from_sql(statement) for statement in functions),
        *(PGTrigger.from_sql(statement) for statement in triggers),
    ]


@cache
def alembic_util_entities() -> list[ReplaceableEntity]:
    """The exact functions and triggers Alembic owns in the public schema.

    Read on first use rather than at import, so importing this module — which the migration
    revisions do at collection time — touches no disk.
    """
    return parse_entities(ENTITY_SQL_PATH.read_text(encoding="utf-8"))
