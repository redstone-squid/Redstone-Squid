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

EXPECTED_FUNCTIONS = 15
EXPECTED_TRIGGERS = 46


def parse_entities(
    sql: str, *, expected_functions: int = EXPECTED_FUNCTIONS, expected_triggers: int = EXPECTED_TRIGGERS
) -> list[ReplaceableEntity]:
    """Parse the PostgreSQL functions and triggers managed by Alembic.

    Require the expected counts so an unrecognized statement cannot silently
    disappear from migration metadata. Update the counts when adding or removing
    an entity. Raise `RuntimeError` when either count differs.
    """
    functions = re.findall(r"^CREATE FUNCTION .*?\$\$;", sql, flags=re.MULTILINE | re.DOTALL)
    triggers = re.findall(r"^CREATE (?:CONSTRAINT )?TRIGGER .*?;$", sql, flags=re.MULTILINE)
    if len(functions) != expected_functions or len(triggers) != expected_triggers:
        msg = (
            f"postgres_entities.sql must define exactly {expected_functions} functions and "
            f"{expected_triggers} triggers; parsed {len(functions)} and {len(triggers)}"
        )
        raise RuntimeError(msg)
    return [
        *(PGFunction.from_sql(statement) for statement in functions),
        *(PGTrigger.from_sql(statement) for statement in triggers),
    ]


@cache
def current_entities() -> list[ReplaceableEntity]:
    """Read and cache the PostgreSQL functions and triggers managed by Alembic.

    Read on first use so importing a migration revision does not read the SQL file.
    """
    return parse_entities(ENTITY_SQL_PATH.read_text(encoding="utf-8"))


@cache
def alembic_util_entities() -> list[ReplaceableEntity]:
    """Return the frozen definitions imported by migrations before the schema audit.

    Keep this entry point stable: older revisions run before the queue column
    renames, so using current definitions would install triggers that reference
    columns which do not yet exist. New revisions embed their SQL explicitly.
    """
    path = Path(__file__).with_name("pre_schema_audit_entities.sql")
    return parse_entities(path.read_text(encoding="utf-8"), expected_functions=15, expected_triggers=46)
