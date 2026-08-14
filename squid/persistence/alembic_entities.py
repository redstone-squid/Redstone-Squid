"""Replaceable PostgreSQL functions and triggers managed by Alembic."""

import re
from pathlib import Path

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

_ENTITY_SQL = Path(__file__).with_name("postgres_entities.sql").read_text(encoding="utf-8")
_FUNCTION_SQL = re.findall(r"^CREATE FUNCTION .*?\$\$;", _ENTITY_SQL, flags=re.MULTILINE | re.DOTALL)
_TRIGGER_SQL = re.findall(r"^CREATE TRIGGER .*?;$", _ENTITY_SQL, flags=re.MULTILINE)

ALEMBIC_UTIL_ENTITIES: list[ReplaceableEntity] = [
    *(PGFunction.from_sql(statement) for statement in _FUNCTION_SQL),
    *(PGTrigger.from_sql(statement) for statement in _TRIGGER_SQL),
]
"""The exact functions and triggers Alembic owns in the public schema."""

if len(_FUNCTION_SQL) != 16 or len(_TRIGGER_SQL) != 39:
    msg = "postgres_entities.sql must define exactly 16 functions and 39 triggers"
    raise RuntimeError(msg)
