"""Integration coverage for the portable Alembic migration chain."""

from collections.abc import Iterator

import psycopg2
import pytest
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES
from squid.worker.queue_health import QUEUE_HEALTH_SQL

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
            outdated_messages_function = connection.execute(
                text("SELECT to_regprocedure('public.get_outdated_messages(bigint)')")
            ).scalar_one()
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
            legacy_taxonomy_tables = {
                table_name: connection.execute(text(f"SELECT to_regclass('public.{table_name}')")).scalar_one()
                for table_name in (
                    "build_restrictions",
                    "build_types",
                    "restriction_aliases",
                    "restrictions",
                    "types",
                )
            }
            legacy_taxonomy_routines = {
                signature: connection.execute(text(f"SELECT to_regprocedure('public.{signature}')")).scalar_one()
                for signature in ("find_restriction_ids(text[])", "sync_new_restriction()")
            }
            retirement_rebuild_queued = connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM record_recompute_queue "
                    "WHERE scope_key = 'door' "
                    "AND reasons @> '[\"legacy_cache_retirement\"]'::jsonb"
                    ")"
                )
            ).scalar_one()
            queue_health_names = {row.queue for row in connection.execute(text(QUEUE_HEALTH_SQL)).mappings()}
            connection.execute(text("INSERT INTO server_settings (server_id) VALUES (999)"))
            connection.execute(
                text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', '42')")
            )
            connection.execute(
                text(
                    "INSERT INTO messages ("
                    "id, server_id, channel_id, author_id, purpose, projection_resource_kind, projection_source_key"
                    ") VALUES (100, 999, 200, 300, 'view_confirmed_build', 'build', '42')"
                )
            )
            initial_projection_state = connection.execute(
                text("SELECT desired_action, desired_revision, applied_revision FROM messages WHERE id = 100")
            ).one()
            connection.execute(
                text(
                    "UPDATE discord_sync_queue "
                    "SET action = 'delete', enqueued_at = enqueued_at + interval '1 second' "
                    "WHERE resource_kind = 'build' AND source_key = '42'"
                )
            )
            updated_projection_state = connection.execute(
                text(
                    "SELECT m.desired_action, m.desired_revision, m.applied_revision, q.generation "
                    "FROM messages m JOIN discord_sync_queue q "
                    "ON q.resource_kind = m.projection_resource_kind AND q.source_key = m.projection_source_key "
                    "WHERE m.id = 100"
                )
            ).one()
    finally:
        engine.dispose()

    expected_functions = {
        entity.signature.partition("(")[0] for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGFunction"
    }
    expected_triggers = {entity.signature for entity in ALEMBIC_UTIL_ENTITIES if type(entity).__name__ == "PGTrigger"}
    assert expected_functions <= function_names
    assert outdated_messages_function is None
    assert trigger_names == expected_triggers
    assert option_table == "vote_session_options"
    assert legacy_record_table is None
    assert legacy_record_routines == set()
    assert set(legacy_taxonomy_tables.values()) == {None}
    assert set(legacy_taxonomy_routines.values()) == {None}
    assert retirement_rebuild_queued is True
    assert queue_health_names == {
        "discord_sync",
        "domain_events.core",
        "domain_events.discord",
        "record_recomputation",
        "schematic_jobs",
        "schematic_renders",
        "search_embeddings",
        "search_projections",
    }
    assert initial_projection_state == ("refresh", 1, 1)
    assert updated_projection_state == ("delete", 2, 1, 2)


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


def test_taxonomy_cutover_refuses_unimported_legacy_rows(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The destructive taxonomy migration fails before dropping unmatched legacy data."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "b8c9d0e1f2a3")

    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO restrictions (build_category, name, type) "
                    "VALUES ('Door', 'Migration parity sentinel', 'miscellaneous')"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(DBAPIError, match="restriction definitions are not fully imported"):
        command.upgrade(config, "head")
