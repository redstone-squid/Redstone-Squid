"""Include PostgreSQL functions and triggers in Alembic's schema comparison.

SQLAlchemy's table metadata does not describe these database objects. Read their
desired definitions from postgres_entities.sql and wrap them as alembic-utils
entities so autogenerate can detect additions, removals, and changed definitions.
"""

import re
from functools import cache
from pathlib import Path

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

ENTITY_SQL_PATH = Path(__file__).with_name("postgres_entities.sql")
"""Sole definition of the entities Alembic owns in the public schema."""

EXPECTED_FUNCTIONS = 14
EXPECTED_TRIGGERS = 45


def parse_entities(sql: str) -> list[ReplaceableEntity]:
    """Parse the PostgreSQL functions and triggers managed by Alembic.

    Require the expected counts so an unrecognized statement cannot silently
    disappear from migration metadata. Update the counts when adding or removing
    an entity. Raise `RuntimeError` when either count differs.
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
    """Read and cache the PostgreSQL functions and triggers managed by Alembic.

    Read on first use so importing a migration revision does not read the SQL file.
    """
    return parse_entities(ENTITY_SQL_PATH.read_text(encoding="utf-8"))
