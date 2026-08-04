"""Integration coverage for the portable Alembic migration chain."""

from collections.abc import Iterator

import psycopg2
import pytest
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from alembic import command
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

MIGRATION_DATABASE = "redstone_squid_migrations"


@pytest.fixture
def migration_database_url(postgres_container: PostgresContainer) -> Iterator[str]:
    """Create an isolated database for applying the complete migration chain."""
    admin_url = postgres_container.get_connection_url(driver="psycopg2")
    admin_dsn = admin_url.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{MIGRATION_DATABASE}"')
    finally:
        connection.close()

    migration_url = admin_url.rsplit("/", maxsplit=1)[0] + f"/{MIGRATION_DATABASE}"
    try:
        yield migration_url
    finally:
        connection = psycopg2.connect(admin_dsn)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (MIGRATION_DATABASE,),
                )
                cursor.execute(f'DROP DATABASE "{MIGRATION_DATABASE}"')
        finally:
            connection.close()


def test_migrations_create_schema_without_drift(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean PostgreSQL database reaches head with all managed entities in sync."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")

    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "d9f6a8b2c4e1")

    downgrade_engine = create_engine(migration_database_url)
    try:
        with downgrade_engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('public.generic_vote_sessions')")).scalar_one() is None
            option_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'vote_session_options'"
                    )
                ).scalars()
            )
            assert {"identifier", "guild_id", "label"}.isdisjoint(option_columns)
    finally:
        downgrade_engine.dispose()

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            function_names = set(
                connection.execute(
                    text(
                        "SELECT proname FROM pg_proc "
                        "JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace "
                        "WHERE pg_namespace.nspname = 'public' AND prokind = 'f'"
                    )
                ).scalars()
            )
            trigger_names = set(
                connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "JOIN pg_class ON pg_class.oid = pg_trigger.tgrelid "
                        "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                        "WHERE pg_namespace.nspname = 'public' AND NOT tgisinternal"
                    )
                ).scalars()
            )
            option_table = connection.execute(text("SELECT to_regclass('public.vote_session_options')")).scalar_one()
            legacy_record_table = connection.execute(
                text("SELECT to_regclass('public.smallest_door_records')")
            ).scalar_one()
            legacy_record_routines = set(
                connection.execute(
                    text(
                        "SELECT proname FROM pg_proc "
                        "JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace "
                        "WHERE pg_namespace.nspname = 'public' "
                        "AND proname IN ("
                        "'enqueue_legacy_record_search_projection', "
                        "'rebuild_smallest_door_records', "
                        "'refresh_smallest_after_door_delete', "
                        "'refresh_smallest_for_door_insert', "
                        "'trg_refresh_smallest_door', "
                        "'trg_refresh_smallest_door_from_builds'"
                        ")"
                    )
                ).scalars()
            )
            retirement_rebuild_queued = connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM record_recompute_queue "
                    "WHERE scope_key = 'door' "
                    "AND reasons @> '[\"legacy_cache_retirement\"]'::jsonb"
                    ")"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    expected_functions = {
        entity.signature.partition("(")[0] for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGFunction"
    }
    expected_triggers = {entity.signature for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGTrigger"}
    assert expected_functions <= function_names
    assert trigger_names == expected_triggers
    assert option_table == "vote_session_options"
    assert legacy_record_table is None
    assert legacy_record_routines == set()
    assert retirement_rebuild_queued is True


def test_alembic_detects_managed_function_and_trigger_drift(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changed functions and missing triggers are surfaced by Alembic autogenerate."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "head")

    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION public.update_updated_at_column()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        RETURN NEW;
                    END;
                    $$
                    """
                )
            )
            connection.execute(text("DROP TRIGGER update_messages_updated_at ON public.messages"))
    finally:
        engine.dispose()

    with pytest.raises(CommandError, match="New upgrade operations detected"):
        command.check(config)
