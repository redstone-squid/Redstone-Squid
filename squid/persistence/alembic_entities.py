"""Include PostgreSQL functions and triggers in Alembic's schema comparison.

SQLAlchemy's table metadata does not describe these database objects. Each SQL
file under postgres_entities defines one alembic-utils entity. The functions and
triggers directories select its type; subdirectories only organize the files.
"""

from functools import cache
from itertools import dropwhile
from pathlib import Path

from alembic_utils.exceptions import SQLParseFailure
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

ENTITY_SQL_DIR = Path(__file__).with_name("postgres_entities")
"""Current definitions of the entities Alembic owns in the public schema."""


def load_entities(directory: Path) -> list[ReplaceableEntity]:
    """Load one entity per SQL file, recursively under functions/ and triggers/.

    Return functions before triggers, sorted by relative path within each type.
    Files may start with blank lines and ordinary SQL line comments. The remaining
    SQL goes directly to alembic-utils; this loader does not validate SQL syntax or
    split statements. Keep exactly one CREATE definition in each file.

    Reject missing or empty directories, SQL files outside the two type directories,
    unparseable definitions, and duplicate entity identities. Non-SQL files are ignored.
    """
    if not directory.is_dir():
        msg = f"Entity directory does not exist: {directory}"
        raise FileNotFoundError(msg)

    entity_types: dict[str, type[ReplaceableEntity]] = {"functions": PGFunction, "triggers": PGTrigger}
    entities: list[ReplaceableEntity] = []
    sources: dict[str, Path] = {}
    for path in sorted(directory.rglob("*.sql")):
        relative = path.relative_to(directory)
        entity_type = entity_types.get(relative.parts[0])
        if entity_type is None:
            msg = f"SQL entity must be under functions/ or triggers/: {path}"
            raise ValueError(msg)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        sql = "".join(dropwhile(lambda line: not line.strip() or line.lstrip().startswith("--"), lines)).strip()
        try:
            entity = entity_type.from_sql(sql)
        except SQLParseFailure as exc:
            msg = f"Cannot parse SQL entity: {path}"
            raise ValueError(msg) from exc
        if entity.identity in sources:
            msg = f"Duplicate SQL entity {entity.identity}: {sources[entity.identity]} and {path}"
            raise ValueError(msg)
        sources[entity.identity] = path
        entities.append(entity)
    if not entities:
        msg = f"No SQL entities found in {directory}"
        raise ValueError(msg)
    return entities


@cache
def current_entities() -> list[ReplaceableEntity]:
    """Read and cache the current definitions on first use, not at import time."""
    return load_entities(ENTITY_SQL_DIR)


@cache
def alembic_util_entities() -> list[ReplaceableEntity]:
    """Return the frozen definitions imported by migrations before the schema audit.

    Keep this entry point stable: older revisions run before the queue column
    renames, so using current definitions would install triggers that reference
    columns which do not yet exist. New revisions embed their SQL explicitly.
    """
    return load_entities(Path(__file__).with_name("pre_schema_audit_entities"))
