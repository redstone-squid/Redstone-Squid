"""Loading of the replaceable entities Alembic owns in the public schema."""

from pathlib import Path

import pytest
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from squid.persistence.alembic_entities import alembic_util_entities, current_entities, load_entities

FUNCTION = """CREATE FUNCTION public.touch() RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
    -- A semicolon inside the body is not a file boundary.
    RETURN NEW;
END;
$body$;
"""
TRIGGER = """CREATE TRIGGER touch
BEFORE UPDATE ON public.builds
FOR EACH ROW EXECUTE FUNCTION public.touch();
"""
CONSTRAINT_TRIGGER = """CREATE CONSTRAINT TRIGGER touch
AFTER UPDATE ON public.builds
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.touch();
"""


def _write(directory: Path, relative: str, sql: str) -> Path:
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql, encoding="utf-8")
    return path


def test_loads_nested_files_in_deterministic_order(tmp_path: Path) -> None:
    _write(tmp_path, "triggers/builds/touch.sql", TRIGGER)
    _write(tmp_path, "functions/nested/deeper/z.sql", FUNCTION.replace("touch()", "last()"))
    _write(tmp_path, "functions/a.sql", FUNCTION)
    _write(tmp_path, "README.md", "These files each define one entity.")

    entities = load_entities(tmp_path)

    assert [entity.signature for entity in entities] == ["touch()", "last()", "touch"]
    assert [type(entity) for entity in entities] == [PGFunction, PGFunction, PGTrigger]
    assert entities[0].definition == PGFunction.from_sql(FUNCTION).definition
    assert entities[2].definition == PGTrigger.from_sql(TRIGGER.strip()).definition


def test_loads_constraint_triggers(tmp_path: Path) -> None:
    _write(tmp_path, "triggers/builds/touch.sql", CONSTRAINT_TRIGGER)

    entity = load_entities(tmp_path)[0]

    assert isinstance(entity, PGTrigger)
    assert entity.is_constraint
    assert entity.on_entity == "public.builds"


@pytest.mark.parametrize(("folder", "sql"), [("functions", FUNCTION), ("triggers", TRIGGER)])
def test_allows_leading_line_comments_and_whitespace(tmp_path: Path, folder: str, sql: str) -> None:
    _write(tmp_path, f"{folder}/touch.sql", "\n  -- Explanation.\n\n-- More context.\n" + sql + "\n")

    entity = load_entities(tmp_path)[0]

    assert entity.signature in {"touch()", "touch"}


@pytest.mark.parametrize("relative", ["touch.sql", "other/touch.sql", "nested/functions/touch.sql"])
def test_rejects_sql_outside_type_directories(tmp_path: Path, relative: str) -> None:
    path = _write(tmp_path, relative, FUNCTION)

    with pytest.raises(ValueError, match="must be under functions/ or triggers/") as error:
        load_entities(tmp_path)

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    ("folder", "sql"),
    [("functions", TRIGGER), ("triggers", FUNCTION), ("functions", "-- Empty file\n"), ("triggers", "SELECT 1;")],
)
def test_reports_the_file_that_cannot_be_parsed(tmp_path: Path, folder: str, sql: str) -> None:
    path = _write(tmp_path, f"{folder}/invalid.sql", sql)

    with pytest.raises(ValueError, match="Cannot parse SQL entity") as error:
        load_entities(tmp_path)

    assert str(path) in str(error.value)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize(("folder", "sql"), [("functions", FUNCTION), ("triggers", TRIGGER)])
def test_rejects_duplicate_entities_across_subfolders(tmp_path: Path, folder: str, sql: str) -> None:
    first = _write(tmp_path, f"{folder}/first.sql", sql)
    second = _write(tmp_path, f"{folder}/nested/second.sql", sql)

    with pytest.raises(ValueError, match="Duplicate SQL entity") as error:
        load_entities(tmp_path)

    assert str(first) in str(error.value)
    assert str(second) in str(error.value)


def test_allows_the_same_trigger_name_on_different_tables(tmp_path: Path) -> None:
    _write(tmp_path, "triggers/builds/touch.sql", TRIGGER)
    _write(tmp_path, "triggers/doors/touch.sql", TRIGGER.replace("public.builds", "public.doors"))

    assert len(load_entities(tmp_path)) == 2


def test_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Entity directory does not exist"):
        load_entities(tmp_path / "missing")


def test_rejects_empty_directory(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Missing SQL must not look like all entities were intentionally removed.")

    with pytest.raises(ValueError, match="No SQL entities found"):
        load_entities(tmp_path)


def test_loads_current_and_frozen_definitions() -> None:
    current = current_entities()
    historical = alembic_util_entities()

    assert all(entity.schema == "public" and entity.signature for entity in [*current, *historical])
    assert any(entity.signature == "delete_vote_session_after_build_vote_session_delete()" for entity in current)
    assert any(entity.signature == "delete_orphaned_build_vote_sessions_after_builds_delete()" for entity in historical)
    assert not any(entity.signature == "delete_vote_session_after_build_vote_session_delete()" for entity in historical)
