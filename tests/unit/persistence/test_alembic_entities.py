"""Parsing of the replaceable entities Alembic owns in the public schema."""

import pytest

from squid.persistence.alembic_entities import (
    ENTITY_SQL_PATH,
    EXPECTED_FUNCTIONS,
    EXPECTED_TRIGGERS,
    alembic_util_entities,
    parse_entities,
)

FUNCTION = """CREATE FUNCTION public.touch_{index}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RETURN NEW;
END;
$$;
"""
TRIGGER = (
    "CREATE TRIGGER touch_{index} BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.touch_{index}();"
)
CONSTRAINT_TRIGGER = (
    "CREATE CONSTRAINT TRIGGER touch_{index} AFTER UPDATE ON public.builds "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.touch_{index}();"
)


def _sql(*, functions: int, triggers: int) -> str:
    statements = [FUNCTION.format(index=index) for index in range(functions)]
    statements += [TRIGGER.format(index=index) for index in range(triggers)]
    return "\n".join(statements) + "\n"


def test_a_complete_definition_parses_into_functions_and_triggers() -> None:
    entities = parse_entities(_sql(functions=EXPECTED_FUNCTIONS, triggers=EXPECTED_TRIGGERS))

    assert len(entities) == EXPECTED_FUNCTIONS + EXPECTED_TRIGGERS
    assert all(entity.schema == "public" for entity in entities)


def test_a_constraint_trigger_is_not_silently_dropped() -> None:
    sql = _sql(functions=EXPECTED_FUNCTIONS, triggers=EXPECTED_TRIGGERS).replace(
        TRIGGER.format(index=0), CONSTRAINT_TRIGGER.format(index=0)
    )

    entities = parse_entities(sql)

    assert any(getattr(entity, "is_constraint", False) for entity in entities)


@pytest.mark.parametrize(
    ("functions", "triggers"),
    [
        (EXPECTED_FUNCTIONS - 1, EXPECTED_TRIGGERS),
        (EXPECTED_FUNCTIONS, EXPECTED_TRIGGERS - 1),
        (EXPECTED_FUNCTIONS + 1, EXPECTED_TRIGGERS),
    ],
)
def test_a_miscounted_definition_is_rejected(functions: int, triggers: int) -> None:
    """A statement the patterns miss is dropped silently, so the counts are the only guard.

    Without it a migration would replace a shorter list than it believed and leave an entity
    running its previous definition.
    """
    with pytest.raises(RuntimeError, match="must define exactly"):
        parse_entities(_sql(functions=functions, triggers=triggers))


def test_an_unterminated_function_body_is_not_silently_dropped() -> None:
    """`$$`-quoted bodies are matched non-greedily across lines; a missing terminator must fail."""
    truncated = _sql(functions=EXPECTED_FUNCTIONS, triggers=EXPECTED_TRIGGERS).replace("$$;", "$$", 1)

    with pytest.raises(RuntimeError, match="must define exactly"):
        parse_entities(truncated)


def test_the_shipped_definition_matches_the_expected_counts() -> None:
    entities = alembic_util_entities()

    assert ENTITY_SQL_PATH.is_file()
    assert len(entities) == EXPECTED_FUNCTIONS + EXPECTED_TRIGGERS
    assert all(entity.signature for entity in entities)
