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
    finally:
        engine.dispose()

    expected_functions = {
        entity.signature.partition("(")[0] for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGFunction"
    }
    expected_triggers = {entity.signature for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGTrigger"}
    assert expected_functions <= function_names
    assert trigger_names == expected_triggers
    assert option_table == "vote_session_options"


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
