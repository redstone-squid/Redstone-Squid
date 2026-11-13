"""Integration coverage for the portable Alembic migration chain."""

import json
from collections.abc import Iterator
from io import StringIO
from uuid import uuid4

import psycopg2
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command
from squid.persistence.alembic_entities import current_entities
from squid.persistence.migration_history import (
    HISTORY_TABLE,
    adopt_script_digests,
    divergent_revisions,
    repair_script_digests,
    script_digest,
)
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.payload_integrity import submission_payload_digest
from squid.worker.queue_health import QUEUE_HEALTH_STATEMENT

MIGRATION_DATABASE = "redstone_squid_migrations"
DYNAMIC_VOTING_REVISION = "4c9e7a2b1d63"
"""The migration whose downgrade path the drift test also exercises."""

NULLABLE_VOTE_THRESHOLDS_REVISION = "b2c3d4e5f6a8"
VOTE_SUBTYPE_INVARIANT_REVISION = "a9b5c8d3e6f0"
IDEMPOTENCY_CALLER_REVISION = "b0c6d9e4f7a1"
FINALIZATION_RESULT_METADATA_REVISION = "c1d7e0f5a8b2"
SCHEMATIC_RENDER_PROJECTION_REVISION = "e1f2a3b4c5d6"
SCHEMATIC_PUBLICATION_REVISION = "f8b9c0d1e2f3"
NOTIFICATION_DELIVERY_OWNER_REVISION = "b5d9f2a7c0e3"
NOTIFICATION_DELIVERY_OWNER_PARENT_REVISION = "a4c8e1f6b9d2"
IMMUTABLE_FINALIZATION_INPUT_REVISION = "c8e2a5b0d3f6"
QUEUE_CLAIM_CHECK_REVISION = "f3c6b9e4a1d7"

pytestmark = pytest.mark.filterwarnings("ignore:Expression #.* detected to include an operator clause:UserWarning")
"""Autogenerate cannot compare an index expression carrying an operator class.

`search_document_facets_text_prefix_idx` declares `text_pattern_ops` inline in
`squid/search/models.py`. Suppress the comparison warning for that known limitation;
these drift checks do not verify the skipped index expression.
"""


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
    """Check a fresh schema, downgrade through dynamic voting, and upgrade without drift.

    Reuse the migrated database to exercise both directions of the chain.
    """
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")

    command.upgrade(config, "head")
    command.check(config)
    # Expressed relative to the migration under test rather than by naming whichever
    # revision currently precedes it, so inserting an earlier revision cannot silently
    # retarget this to undo a different migration.
    command.downgrade(config, f"{DYNAMIC_VOTING_REVISION}-1")

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
            queue_health_before = {row.queue: row for row in connection.execute(QUEUE_HEALTH_STATEMENT).mappings()}
            connection.execute(
                text(
                    "INSERT INTO discord_sync_queue (resource_kind, source_key, claimed_at, claim_token) "
                    "VALUES ('vote_session', 'expired-health-claim', now() - interval '6 minutes', gen_random_uuid())"
                )
            )
            queue_health_after = {row.queue: row for row in connection.execute(QUEUE_HEALTH_STATEMENT).mappings()}
            connection.execute(text("INSERT INTO server_settings (server_id) VALUES (999)"))
            # A post records only the generation it was rendered at; what it *should*
            # show lives on the queue row, so staleness is a join rather than a desired
            # revision written onto the post by a trigger.
            seeded_generation = connection.execute(
                text(
                    "INSERT INTO discord_sync_queue (resource_kind, source_key) "
                    "VALUES ('build', '42') RETURNING generation"
                )
            ).scalar_one()
            connection.execute(
                text("INSERT INTO messages (id, guild_id, channel_id, author_id) VALUES (100, 999, 200, 300)")
            )
            connection.execute(
                text(
                    "INSERT INTO discord_posts ("
                    "message_id, channel_id, resource_kind, resource_key, surface, applied_revision"
                    ") VALUES (100, 200, 'build', '42', 'build_card', 0)"
                )
            )
            stale_post = connection.execute(
                text(
                    "SELECT p.applied_revision < q.generation "
                    "FROM discord_posts p JOIN discord_sync_queue q "
                    "ON q.resource_kind = p.resource_kind AND q.source_key = p.resource_key "
                    "WHERE p.message_id = 100"
                )
            ).scalar_one()
            reissued_generation = connection.execute(
                text(
                    "UPDATE discord_sync_queue "
                    "SET action = 'delete', enqueued_at = enqueued_at + interval '1 second' "
                    "WHERE resource_kind = 'build' AND source_key = '42' RETURNING generation"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    expected_functions = {
        entity.signature.partition("(")[0] for entity in current_entities() if type(entity).__name__ == "PGFunction"
    }
    expected_triggers = {entity.signature for entity in current_entities() if type(entity).__name__ == "PGTrigger"}
    assert expected_functions <= function_names
    assert outdated_messages_function is None
    assert trigger_names == expected_triggers
    assert option_table == "vote_session_options"
    assert legacy_record_table is None
    assert legacy_record_routines == set()
    assert set(legacy_taxonomy_tables.values()) == {None}
    assert set(legacy_taxonomy_routines.values()) == {None}
    assert retirement_rebuild_queued is True
    assert set(queue_health_before) == {
        "discord_sync",
        "domain_events.core",
        "domain_events.discord",
        "record_recomputation",
        "schematic_jobs",
        "schematic_renders",
        "search_embeddings",
        "search_projections",
    }
    assert queue_health_after["discord_sync"].ready == queue_health_before["discord_sync"].ready + 1
    assert queue_health_after["discord_sync"].in_flight == queue_health_before["discord_sync"].in_flight
    # Sequence values are not fixed. A lower applied revision is stale; this direct
    # UPDATE changes action and enqueue time without issuing a new generation.
    assert stale_post is True
    assert reissued_generation == seeded_generation


def test_notification_delivery_owner_migration_rejects_corruption_and_downgrades(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require each delivery to belong to the same account as its notification.

    Upgrade rejects existing ownership mismatches; downgrade restores the foreign key
    that checked only whether the notification existed.
    """
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{NOTIFICATION_DELIVERY_OWNER_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            owner_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            other_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            event_id = connection.execute(
                text(
                    "INSERT INTO domain_events (event_type, aggregate_kind, aggregate_id) "
                    "VALUES ('build.confirmed', 'build', 42) RETURNING id"
                )
            ).scalar_one()
            notification_id = connection.execute(
                text(
                    "INSERT INTO notifications (account_id, event_id, source_key, kind, payload, web_visible) "
                    "VALUES (:owner_id, :event_id, 'event:migration:owner', 'build_confirmed', "
                    "'{\"build_id\": 42}'::jsonb, true) RETURNING id"
                ),
                {"owner_id": owner_id, "event_id": event_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO notification_deliveries (notification_id, account_id) "
                    "VALUES (:notification_id, :other_id)"
                ),
                {"notification_id": notification_id, "other_id": other_id},
            )

        # Simulate a process that completed the autocommitted concurrent index build but
        # stopped before Alembic attached the constraint or advanced the revision.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text("CREATE UNIQUE INDEX CONCURRENTLY notifications_id_account_key ON notifications (id, account_id)")
            )

        with pytest.raises(DBAPIError, match="ownership mismatch blocks composite foreign key"):
            command.upgrade(config, NOTIFICATION_DELIVERY_OWNER_REVISION)

        with engine.connect() as connection:
            constraint_names = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conname IN ("
                        "'notifications_id_account_key', "
                        "'notification_deliveries_notification_id_fkey', "
                        "'notification_deliveries_notification_owner_fkey')"
                    )
                ).scalars()
            )
            interrupted_index = connection.execute(
                text("SELECT to_regclass('public.notifications_id_account_key')")
            ).scalar_one()
        assert constraint_names == {"notification_deliveries_notification_id_fkey"}
        assert interrupted_index == "notifications_id_account_key"

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE notification_deliveries SET account_id = :owner_id WHERE notification_id = :notification_id"
                ),
                {"owner_id": owner_id, "notification_id": notification_id},
            )
        command.upgrade(config, NOTIFICATION_DELIVERY_OWNER_REVISION)

        with engine.connect() as connection:
            constraints = {
                row.conname: row
                for row in connection.execute(
                    text(
                        "SELECT conname, convalidated, condeferrable, condeferred, pg_get_constraintdef(oid) AS definition "
                        "FROM pg_constraint WHERE conname IN ("
                        "'notifications_id_account_key', "
                        "'notification_deliveries_notification_id_fkey', "
                        "'notification_deliveries_notification_owner_fkey')"
                    )
                )
            }
        assert set(constraints) == {
            "notifications_id_account_key",
            "notification_deliveries_notification_owner_fkey",
        }
        owner_constraint = constraints["notification_deliveries_notification_owner_fkey"]
        assert owner_constraint.convalidated is True
        assert owner_constraint.condeferrable is True
        assert owner_constraint.condeferred is True
        assert "ON DELETE CASCADE" in owner_constraint.definition

        def commit_mismatched_owner() -> None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE notification_deliveries SET account_id = :other_id "
                        "WHERE notification_id = :notification_id"
                    ),
                    {"notification_id": notification_id, "other_id": other_id},
                )
                # Initially deferred means multi-statement account merges can move the parent
                # and child before commit; a transaction that ends mismatched still fails.
                assert (
                    connection.execute(
                        text("SELECT account_id FROM notification_deliveries WHERE notification_id = :notification_id"),
                        {"notification_id": notification_id},
                    ).scalar_one()
                    == other_id
                )

        with pytest.raises(DBAPIError, match="notification_deliveries_notification_owner_fkey"):
            commit_mismatched_owner()

        command.downgrade(config, f"{NOTIFICATION_DELIVERY_OWNER_REVISION}-1")
        with engine.connect() as connection:
            downgraded_constraints = {
                row.conname: row.definition
                for row in connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                        "WHERE conname IN ('notifications_id_account_key', "
                        "'notification_deliveries_notification_id_fkey', "
                        "'notification_deliveries_notification_owner_fkey')"
                    )
                )
            }
        assert set(downgraded_constraints) == {"notification_deliveries_notification_id_fkey"}
        assert "ON DELETE CASCADE" in downgraded_constraints["notification_deliveries_notification_id_fkey"]
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('public.notifications_id_account_key')")).scalar_one()
                is None
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE notification_deliveries SET account_id = :other_id WHERE notification_id = :id"),
                {"other_id": other_id, "id": notification_id},
            )
        with (
            pytest.raises(DBAPIError, match="notification_deliveries_notification_id_fkey"),
            engine.begin() as connection,
        ):
            connection.execute(
                text("UPDATE notification_deliveries SET notification_id = 999999 WHERE notification_id = :id"),
                {"id": notification_id},
            )
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM notifications WHERE id = :notification_id"),
                {"notification_id": notification_id},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM notification_deliveries WHERE notification_id = :notification_id"),
                    {"notification_id": notification_id},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_notification_delivery_owner_offline_sql_preserves_safe_ddl_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the supporting index and validate ownership before removing the old foreign key."""
    monkeypatch.setenv("SQUID_DATABASE_URL", "postgresql+asyncpg://squid:squid@localhost/squid")
    output = StringIO()
    config = Config("alembic.ini", toml_file="pyproject.toml", output_buffer=output)

    command.upgrade(
        config,
        f"{NOTIFICATION_DELIVERY_OWNER_PARENT_REVISION}:{NOTIFICATION_DELIVERY_OWNER_REVISION}",
        sql=True,
    )

    sql = output.getvalue()
    concurrent_index = sql.index("CREATE UNIQUE INDEX CONCURRENTLY notifications_id_account_key")
    attach_constraint = sql.index("UNIQUE USING INDEX notifications_id_account_key")
    not_valid = sql.index("NOT VALID")
    validate_constraint = sql.index("VALIDATE CONSTRAINT notification_deliveries_notification_owner_fkey")
    drop_legacy = sql.index("DROP CONSTRAINT notification_deliveries_notification_id_fkey")
    assert concurrent_index < not_valid < validate_constraint < attach_constraint < drop_legacy


def test_applied_schematic_render_projection_migration_upgrades_and_downgrades(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The applied historical revision retains its filename and reversible schema contract."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{SCHEMATIC_RENDER_PROJECTION_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            # Historical revisions import current trigger bodies, which require newer columns.
            # Isolate this schema migration from those unrelated delivery triggers.
            connection.execute(text("ALTER TABLE builds DISABLE TRIGGER builds_emit_domain_event"))
            connection.execute(text("ALTER TABLE builds DISABLE TRIGGER builds_enqueue_discord_sync"))
            connection.execute(text("ALTER TABLE builds DISABLE TRIGGER builds_enqueue_search"))
            user_id = connection.execute(
                text("INSERT INTO users (discord_id) VALUES (1830001) RETURNING id")
            ).scalar_one()
            build_id = connection.execute(
                text(
                    "INSERT INTO builds (submission_status, submitter_user_id, ai_generated) "
                    "VALUES (0, :user_id, false) RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()
            digest = "a" * 64
            connection.execute(
                text(
                    "INSERT INTO schematic_files (sha256, data, byte_size, source_format) "
                    "VALUES (:digest, :data, 1, 'litematic')"
                ),
                {"digest": digest, "data": b"x"},
            )
            schematic_id = connection.execute(
                text(
                    "INSERT INTO build_schematics ("
                    "build_id, file_sha256, is_primary, width, height, length, "
                    "allocated_width, allocated_height, allocated_length, block_count, bounding_volume, "
                    "entity_count, palette_size, region_names, signs, analyzer_version, analysis_schema_version"
                    ") VALUES ("
                    ":build_id, :digest, true, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, ARRAY['Main'], "
                    "'[]'::jsonb, 'migration-test', 1"
                    ") RETURNING id"
                ),
                {"build_id": build_id, "digest": digest},
            ).scalar_one()
            render_id = connection.execute(
                text(
                    "INSERT INTO schematic_renders ("
                    "build_schematic_id, recipe_hash, url, width, height, byte_size"
                    ") VALUES (:schematic_id, :recipe, 'https://example.invalid/preview.png', 1, 1, 1) "
                    "RETURNING id"
                ),
                {"schematic_id": schematic_id, "recipe": "b" * 64},
            ).scalar_one()

        command.upgrade(config, SCHEMATIC_RENDER_PROJECTION_REVISION)
        with engine.begin() as connection:
            render_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'schematic_renders'"
                    )
                ).scalars()
            )
            assert "object_key" in render_columns
            assert connection.execute(text("SELECT build_id FROM schematic_render_queue")).scalars().all() == [build_id]
            connection.execute(
                text("UPDATE schematic_renders SET object_key = 'renders/migrated.png' WHERE id = :id"),
                {"id": render_id},
            )
            assert (
                connection.execute(
                    text("SELECT object_key FROM schematic_renders WHERE id = :id"), {"id": render_id}
                ).scalar_one()
                == "renders/migrated.png"
            )

        command.downgrade(config, f"{SCHEMATIC_RENDER_PROJECTION_REVISION}-1")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('public.schematic_render_queue')")).scalar_one() is None
            downgraded_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'schematic_renders'"
                    )
                ).scalars()
            )
            assert "object_key" not in downgraded_columns
    finally:
        engine.dispose()


def test_schematic_file_schema_ceiling_agrees_with_its_migration(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, SCHEMATIC_PUBLICATION_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            constraint = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'schematic_files_size_bounded'"
                )
            ).scalar_one()
            assert str(SCHEMATIC_FILE_SCHEMA_MAX_BYTES) in constraint
            connection.execute(
                text(
                    "INSERT INTO schematic_files (sha256, data, byte_size, source_format) "
                    "VALUES (:digest, :data, :byte_size, 'litematic')"
                ),
                {"digest": "c" * 64, "data": b"x", "byte_size": SCHEMATIC_FILE_SCHEMA_MAX_BYTES},
            )

        with engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO schematic_files (sha256, data, byte_size, source_format) "
                    "VALUES (:digest, :data, :byte_size, 'litematic')"
                ),
                {"digest": "d" * 64, "data": b"x", "byte_size": SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1},
            )
    finally:
        engine.dispose()


def test_nullable_vote_thresholds_migrate_sentinels_in_both_directions(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic-poll sentinels become NULL and are restored by downgrade."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{NULLABLE_VOTE_THRESHOLDS_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            # Historical revisions import current trigger bodies, which require newer columns.
            # Isolate this schema migration from those unrelated delivery triggers.
            connection.execute(text("ALTER TABLE vote_sessions DISABLE TRIGGER vote_sessions_enqueue_discord_sync"))
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(text("INSERT INTO server_settings (server_id) VALUES (503)"))
            vote_session_id = connection.execute(
                text(
                    "INSERT INTO vote_sessions "
                    "(status, result, author_account_id, kind, pass_threshold, fail_threshold) "
                    "VALUES ('open', 'pending', :author, 'generic', 32767, -32768) RETURNING id"
                ),
                {"author": account_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO generic_vote_sessions "
                    "(vote_session_id, guild_id, question, visibility, deadline) "
                    "VALUES (:id, 503, 'Migration?', 'anonymous_live', now() + interval '1 hour')"
                ),
                {"id": vote_session_id},
            )

        command.upgrade(config, NULLABLE_VOTE_THRESHOLDS_REVISION)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT pass_threshold, fail_threshold FROM vote_sessions WHERE id = :id"),
                {"id": vote_session_id},
            ).one() == (None, None)
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("UPDATE vote_sessions SET pass_threshold = 1, fail_threshold = -1 WHERE id = :id"),
                    {"id": vote_session_id},
                )
            connection.rollback()

        command.downgrade(config, f"{NULLABLE_VOTE_THRESHOLDS_REVISION}-1")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT pass_threshold, fail_threshold FROM vote_sessions WHERE id = :id"),
                {"id": vote_session_id},
            ).one() == (32767, -32768)
    finally:
        engine.dispose()


def test_queue_claim_check_migration_gives_tokenless_claims_a_token(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim made by code from before claim tokens must not block adding the claim check."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{QUEUE_CLAIM_CHECK_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO record_recompute_queue (scope_key, build_kind, claimed_at) "
                    "VALUES ('door', 'door', now()) "
                    "ON CONFLICT (scope_key) DO UPDATE SET claimed_at = now(), claim_token = NULL"
                )
            )

        command.upgrade(config, QUEUE_CLAIM_CHECK_REVISION)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT claim_token IS NOT NULL FROM record_recompute_queue WHERE scope_key = 'door'")
            ).scalar_one()
    finally:
        engine.dispose()


def test_vote_subtype_invariant_upgrade_rejects_existing_incoherence(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject existing voting sessions without the detail row required by their kind."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{VOTE_SUBTYPE_INVARIANT_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO vote_sessions "
                    "(status, result, author_account_id, kind, pass_threshold, fail_threshold) "
                    "VALUES ('open', 'pending', :author, 'generic', NULL, NULL)"
                ),
                {"author": account_id},
            )

        with pytest.raises(DBAPIError, match="cannot enforce vote session subtype kinds"):
            command.upgrade(config, VOTE_SUBTYPE_INVARIANT_REVISION)
    finally:
        engine.dispose()


def test_vote_subtype_invariant_downgrade_removes_triggers_and_function(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The aggregate invariant is fully reversible when rolling back its revision."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, VOTE_SUBTYPE_INVARIANT_REVISION)
    command.downgrade(config, f"{VOTE_SUBTYPE_INVARIANT_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regprocedure('public.enforce_vote_session_kind_subtype()')")
                ).scalar_one()
                is None
            )
            triggers = set(
                connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "JOIN pg_class ON pg_class.oid = pg_trigger.tgrelid "
                        "WHERE tgname LIKE '%_kind_subtype_check' AND NOT tgisinternal"
                    )
                ).scalars()
            )
            assert triggers == set()
    finally:
        engine.dispose()


def test_idempotency_caller_migration_dual_writes_and_downgrades(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old and new API binaries share one caller namespace during the rollout window."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{IDEMPOTENCY_CALLER_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO idempotency_requests "
                    "(id, principal, idempotency_key, request_fingerprint, method, route, expires_at) VALUES "
                    "('11111111-1111-4111-8111-111111111111', 'account:old', 'old', '\\x01', "
                    "'POST', '/v1/builds', now() + interval '1 day')"
                )
            )

        command.upgrade(config, IDEMPOTENCY_CALLER_REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO idempotency_requests "
                    "(id, caller, idempotency_key, request_fingerprint, method, route, expires_at) VALUES "
                    "('22222222-2222-4222-8222-222222222222', 'account:new', 'new', '\\x02', "
                    "'DELETE', '/v1/builds/2', now() + interval '1 day')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO idempotency_requests "
                    "(id, principal, idempotency_key, request_fingerprint, method, route, expires_at) VALUES "
                    "('33333333-3333-4333-8333-333333333333', 'account:old-rolling', 'rolling', '\\x03', "
                    "'PATCH', '/v1/builds/3', now() + interval '1 day')"
                )
            )
            identities = connection.execute(
                text("SELECT principal, caller FROM idempotency_requests ORDER BY id")
            ).all()
            assert identities == [
                ("account:old", "account:old"),
                ("account:new", "account:new"),
                ("account:old-rolling", "account:old-rolling"),
            ]

        with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "INSERT INTO idempotency_requests "
                        "(id, caller, idempotency_key, request_fingerprint, method, route, expires_at) VALUES "
                        "('44444444-4444-4444-8444-444444444444', 'account:bad', 'bad', '\\x04', "
                        "'TRACE', '/v1/builds', now() + interval '1 day')"
                    )
                )
            connection.rollback()

        command.downgrade(config, f"{IDEMPOTENCY_CALLER_REVISION}-1")
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'idempotency_requests'"
                    )
                ).scalars()
            )
            assert "caller" not in columns
            assert set(connection.execute(text("SELECT principal FROM idempotency_requests")).scalars()) == {
                "account:old",
                "account:new",
                "account:old-rolling",
            }
    finally:
        engine.dispose()


def test_finalization_result_metadata_migration_expands_and_downgrades(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration makes legacy result metadata nullable; downgrade restores both NOT NULL constraints."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{FINALIZATION_RESULT_METADATA_REVISION}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            before = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'submission_finalization_results' "
                        "AND column_name IN ('target_key', 'provenance')"
                    )
                )
            }
            assert before == {"target_key": "NO", "provenance": "NO"}

        command.upgrade(config, FINALIZATION_RESULT_METADATA_REVISION)
        with engine.connect() as connection:
            expanded = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'submission_finalization_results' "
                        "AND column_name IN ('target_key', 'provenance')"
                    )
                )
            }
            assert expanded == {"target_key": "YES", "provenance": "YES"}

        command.downgrade(config, f"{FINALIZATION_RESULT_METADATA_REVISION}-1")
        with engine.connect() as connection:
            downgraded = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'submission_finalization_results' "
                        "AND column_name IN ('target_key', 'provenance')"
                    )
                )
            }
            assert downgraded == before
    finally:
        engine.dispose()


def test_finalization_input_migration_backfills_and_rejects_updates(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing job inputs survive the split and the retained copy cannot be edited."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, f"{IMMUTABLE_FINALIZATION_INPUT_REVISION}-1")
    engine = create_engine(migration_database_url)
    draft_id = "44444444-4444-4444-8444-444444444444"
    job_id = "55555555-5555-4555-8555-555555555555"
    payload: dict[str, object] = {"payload_schema": 2, "target": "door"}
    payload_sha256 = submission_payload_digest(payload)
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO submission_drafts ("
                    "id, owner_account_id, schema_id, schema_revision, category, answers, origin, expires_at"
                    ") VALUES ("
                    ":draft_id, :account_id, 'test', 1, 'door', '{}'::jsonb, 'web', now() + interval '1 day'"
                    ")"
                ),
                {"draft_id": draft_id, "account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs ("
                    "id, draft_id, draft_revision, payload, payload_sha256, status, attention_issues"
                    ") VALUES ("
                    ":job_id, :draft_id, 0, CAST(:payload AS jsonb), :payload_sha256, 'pending', '[]'::jsonb"
                    ")"
                ),
                {
                    "job_id": job_id,
                    "draft_id": draft_id,
                    "payload": json.dumps(payload),
                    "payload_sha256": payload_sha256,
                },
            )

        command.upgrade(config, IMMUTABLE_FINALIZATION_INPUT_REVISION)
        with engine.connect() as connection:
            retained = connection.execute(
                text("SELECT payload, payload_sha256 FROM submission_finalization_inputs WHERE job_id = :job_id"),
                {"job_id": job_id},
            ).one()
            assert retained.payload == payload
            assert retained.payload_sha256 == payload_sha256

        with (
            pytest.raises(DBAPIError, match="submission finalization inputs are immutable"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "UPDATE submission_finalization_inputs "
                    "SET payload = '{\"payload_schema\": 3}'::jsonb WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )

        command.downgrade(config, f"{IMMUTABLE_FINALIZATION_INPUT_REVISION}-1")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT payload FROM submission_finalization_jobs WHERE id = :job_id"),
                    {"job_id": job_id},
                ).scalar_one()
                == payload
            )
            assert (
                connection.execute(text("SELECT to_regclass('public.submission_finalization_inputs')")).scalar_one()
                is None
            )
    finally:
        engine.dispose()


def test_sponsor_migration_refuses_to_discard_retained_provenance(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sponsor downgrade is allowed only after every new provenance field is empty."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "head")
    engine = create_engine(migration_database_url)
    installation_id = "77777777-7777-4777-8777-777777777777"
    draft_id = "88888888-8888-4888-8888-888888888888"
    try:
        with engine.begin() as connection:
            _begin_build_edit(connection)
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            build_id = connection.execute(
                text(
                    "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated, "
                    "sponsor_installation_id, sponsor_display_name) "
                    "VALUES (0, 'Utility', :account_id, false, :installation_id, 'Example server') RETURNING id"
                ),
                {"account_id": account_id, "installation_id": installation_id},
            ).scalar_one()
            connection.execute(text("INSERT INTO utilities (build_id) VALUES (:id)"), {"id": build_id})

        with pytest.raises(DBAPIError, match="cannot downgrade while sponsor attribution schema data is retained"):
            command.downgrade(config, "f4a5b6c7d8e9")

        with engine.begin() as connection:
            _begin_build_edit(connection)
            connection.execute(
                text("UPDATE builds SET sponsor_installation_id = NULL, sponsor_display_name = NULL WHERE id = :id"),
                {"id": build_id},
            )
            connection.execute(
                text(
                    "INSERT INTO submission_drafts (id, owner_account_id, schema_id, schema_revision, category, "
                    "answers, origin, source_installation_id, expires_at) VALUES "
                    "(:draft_id, :account_id, 'test', 1, 'utility', '{}'::jsonb, 'paper', :installation_id, "
                    "now() + interval '1 day')"
                ),
                {"draft_id": draft_id, "account_id": account_id, "installation_id": installation_id},
            )

        with pytest.raises(DBAPIError, match="cannot downgrade while sponsor attribution schema data is retained"):
            command.downgrade(config, "f4a5b6c7d8e9")

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE submission_drafts SET source_installation_id = NULL WHERE id = :id"),
                {"id": draft_id},
            )
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs ("
                    "id, draft_id, draft_revision, payload, status, attention_at, attention_issues"
                    ") VALUES ("
                    "'99999999-9999-4999-8999-999999999999', :draft_id, 0, "
                    "'{\"payload_schema\": 3}'::jsonb, 'needs_attention', now(), "
                    '\'[{"field_id": "submission", "reason": "target_rejected"}]\'::jsonb'
                    ")"
                ),
                {"draft_id": draft_id},
            )

        with pytest.raises(DBAPIError, match="cannot downgrade while sponsor attribution schema data is retained"):
            command.downgrade(config, "f4a5b6c7d8e9")

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM submission_finalization_jobs WHERE draft_id = :draft_id"),
                {"draft_id": draft_id},
            )
        command.downgrade(config, "f4a5b6c7d8e9")
    finally:
        engine.dispose()


def test_sponsor_migration_refuses_unresolved_legacy_attribution(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema-two requests cannot cross the migration without a verified sponsor snapshot."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "f4a5b6c7d8e9")
    engine = create_engine(migration_database_url)
    draft_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO submission_drafts (id, owner_account_id, schema_id, schema_revision, category, "
                    "answers, origin, expires_at) VALUES ("
                    ":draft_id, :account_id, 'test', 1, 'utility', '{}'::jsonb, 'paper', now() + interval '1 day')"
                ),
                {"draft_id": draft_id, "account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs ("
                    "id, draft_id, draft_revision, payload, status, attention_at, attention_issues"
                    ") VALUES ("
                    "'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', :draft_id, 0, "
                    '\'{"payload_schema": 2, "sponsor_attribution": true}\'::jsonb, '
                    "'needs_attention', now(), "
                    '\'[{"field_id": "sponsor_attribution", "reason": "target_rejected"}]\'::jsonb'
                    ")"
                ),
                {"draft_id": draft_id},
            )

        with pytest.raises(DBAPIError, match="cannot migrate unresolved legacy sponsor attribution requests"):
            command.upgrade(config, "head")

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM submission_finalization_jobs WHERE draft_id = :draft_id"), {"draft_id": draft_id}
            )
        command.upgrade(config, "head")

        with (
            pytest.raises(DBAPIError, match="submission_finalization_jobs_legacy_sponsor_forbidden"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs ("
                    "id, draft_id, draft_revision, payload, status, attention_at, attention_issues"
                    ") VALUES ("
                    "'cccccccc-cccc-4ccc-8ccc-cccccccccccc', :draft_id, 0, "
                    '\'{"payload_schema": "2", "sponsor_attribution": "true"}\'::jsonb, '
                    "'needs_attention', now(), "
                    '\'[{"field_id": "sponsor_attribution", "reason": "target_rejected"}]\'::jsonb'
                    ")"
                ),
                {"draft_id": draft_id},
            )
    finally:
        engine.dispose()


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
                    CREATE OR REPLACE FUNCTION public.enqueue_build_search_projection()
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
            connection.execute(text("DROP TRIGGER builds_enqueue_discord_sync ON public.builds"))
    finally:
        engine.dispose()

    with pytest.raises(CommandError, match="New upgrade operations detected"):
        command.check(config)


def test_idempotency_encryption_migration_purges_plaintext_replay_rows(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy response bodies cannot survive into the ciphertext-only schema."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "d2f3a4b5c6d7")

    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO idempotency_requests ("
                    "id, principal, idempotency_key, request_fingerprint, method, route, state, "
                    "response_status, response_headers, response_body, completed_at, expires_at"
                    ") VALUES ("
                    "'11111111-1111-1111-1111-111111111111', 'account:1', 'legacy-secret', "
                    "decode('00', 'hex'), 'POST', '/v1/minecraft/installations', 'completed', 201, "
                    "'{\"content-type\":\"application/json\"}'::jsonb, 'plaintext-secret', now(), "
                    "now() + interval '24 hours'"
                    ")"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM idempotency_requests")).scalar_one() == 0
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'idempotency_requests'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert "response_body" not in columns
    assert {"response_body_ciphertext", "response_body_key_id", "response_body_nonce"} <= columns


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


def test_submission_content_policy_migration_preserves_uploads_and_retry_identity(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing drafts and queued payloads survive removal of content filtering."""
    import hashlib
    import json
    from uuid import uuid4

    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "a3c5e7f9b1d2")
    draft_id, job_id, artifact_id = uuid4(), uuid4(), uuid4()
    payload = {
        "payload_schema": 1,
        "schema_id": "build_submission.v1",
        "schema_revision": 1,
        "schematic_policy": {
            "visibility": "public_download",
            "license": "cc_by_4_0",
            "rights_attested": True,
            "include_inventories": False,
            "include_free_text": False,
        },
        "artifacts": {"sanitized_schematic_id": str(artifact_id), "normalized_media_upload_ids": []},
    }
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO submission_drafts "
                    "(id, owner_account_id, schema_id, schema_revision, category, revision, status, answers, "
                    "origin, expires_at) VALUES (:id, :owner, 'build_submission.v1', 1, 'other', 7, 'processing', "
                    "CAST(:answers AS jsonb), 'web', now() + interval '7 days')"
                ),
                {
                    "id": draft_id,
                    "owner": account_id,
                    "answers": json.dumps({"display_name": "Door", "include_inventories": False}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs "
                    "(id, draft_id, draft_revision, payload, payload_sha256, status, attention_issues) "
                    "VALUES (:id, :draft, 7, CAST(:payload AS jsonb), :digest, 'pending', '[]'::jsonb)"
                ),
                {"id": job_id, "draft": draft_id, "payload": json.dumps(payload), "digest": "a" * 64},
            )
        command.upgrade(config, "b4d6f8a0c2e4")
        with engine.connect() as connection:
            draft = connection.execute(
                text("SELECT schema_revision, revision, answers FROM submission_drafts WHERE id = :id"),
                {"id": draft_id},
            ).one()
            job = connection.execute(
                text("SELECT draft_revision, payload, payload_sha256 FROM submission_finalization_jobs WHERE id = :id"),
                {"id": job_id},
            ).one()
        assert draft == (2, 8, {"display_name": "Door"})
        assert job.draft_revision == draft.revision
        assert job.payload["schema_revision"] == 2
        assert job.payload["payload_schema"] == 2
        assert job.payload["artifacts"] == {"schematic_id": str(artifact_id), "normalized_media_upload_ids": []}
        assert job.payload["schematic_policy"] == {
            "visibility": "public_download",
            "license": "cc_by_4_0",
            "rights_attested": True,
        }
        encoded = json.dumps(job.payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert job.payload_sha256 == hashlib.sha256(encoded).hexdigest()
    finally:
        engine.dispose()


def test_finalization_attempt_migration_preserves_history_and_refuses_lossy_downgrade(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained jobs keep their identity and payload when the one-job bound is removed."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "b5d9f2a7c0e3")
    engine = create_engine(migration_database_url)
    draft_id = "88888888-8888-4888-8888-888888888888"
    job_id = "99999999-9999-4999-8999-999999999999"
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO submission_drafts (id, owner_account_id, schema_id, schema_revision, category, "
                    "answers, origin, expires_at) VALUES "
                    "(:draft_id, :account_id, 'test', 1, 'other', '{}'::jsonb, 'web', now() + interval '1 day')"
                ),
                {"draft_id": draft_id, "account_id": account_id},
            )
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs "
                    "(id, draft_id, draft_revision, payload, status, attention_at, attention_issues) "
                    'VALUES (:job_id, :draft_id, 0, \'{"description": "retained"}\'::jsonb, '
                    '\'needs_attention\', now(), \'[{"field_id": "schematic", '
                    '"reason": "schematic_required"}]\'::jsonb)'
                ),
                {"job_id": job_id, "draft_id": draft_id},
            )
        command.upgrade(config, "c6e0a3b8d1f4")
        with engine.begin() as connection:
            retained = connection.execute(
                text("SELECT id::text, attempt_number, payload FROM submission_finalization_jobs")
            ).one()
            assert retained == (job_id, 1, {"description": "retained"})
        command.downgrade(config, "b5d9f2a7c0e3")
        command.upgrade(config, "c6e0a3b8d1f4")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO submission_finalization_jobs "
                    "(id, draft_id, draft_revision, attempt_number, status, attention_at, attention_issues) "
                    "SELECT gen_random_uuid(), draft_id, draft_revision, 2, status, attention_at, attention_issues "
                    "FROM submission_finalization_jobs WHERE id = :job_id"
                ),
                {"job_id": job_id},
            )
        with pytest.raises(RuntimeError, match="Cannot downgrade while multiple finalization attempts exist"):
            command.downgrade(config, "b5d9f2a7c0e3")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM submission_finalization_jobs")) == 2
        with engine.begin() as connection:
            connection.execute(text("UPDATE submission_drafts SET status = 'needs_attention'"))
        command.upgrade(config, "d7f1b4c9e2a5")
        with engine.connect() as connection:
            issues = connection.scalar(text("SELECT preparation_issues FROM submission_drafts"))
            assert issues == [{"field_id": "schematic", "reason": "schematic_required"}]
            assert connection.scalar(text("SELECT count(*) FROM submission_finalization_jobs")) == 2
        with pytest.raises(RuntimeError, match="Cannot downgrade while draft preparation state is retained"):
            command.downgrade(config, "c6e0a3b8d1f4")
        with engine.begin() as connection:
            connection.execute(text("UPDATE submission_drafts SET preparation_issues = '[]'::jsonb"))
        command.downgrade(config, "c6e0a3b8d1f4")
    finally:
        engine.dispose()


def test_inferred_capacity_migration_preserves_provenance(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "e8a2c5d0f3b6")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO submission_drafts (id, owner_account_id, schema_id, schema_revision, category, "
                    "answers, origin, expires_at) VALUES "
                    "(gen_random_uuid(), :owner, 'test', 1, 'other', '{}'::jsonb, 'discord', now() + interval '7 days')"
                ),
                {"owner": account_id},
            )
            assert connection.scalar(text("SELECT inferred FROM submission_drafts")) is False
            connection.execute(text("UPDATE submission_drafts SET inferred = true"))
        with pytest.raises(RuntimeError, match="Cannot downgrade while inferred drafts are retained"):
            command.downgrade(config, "d7f1b4c9e2a5")
        with engine.begin() as connection:
            connection.execute(text("UPDATE submission_drafts SET inferred = false"))
        command.downgrade(config, "d7f1b4c9e2a5")
        command.upgrade(config, "e8a2c5d0f3b6")
    finally:
        engine.dispose()


def test_migration_history_records_the_chain_and_guards_edited_scripts(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration ledger records applied steps and rejects changed scripts.

    Reuse one database at head for the ledger checks. Corrupt stored digests to simulate
    script changes without modifying migration files in the working tree.
    """
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    # BuildConfig requires the pair, which is what the image supplies.
    monkeypatch.setenv("SQUID_BUILD_COMMIT_HASH", "0123456789abcdef")
    monkeypatch.setenv("SQUID_BUILD_COMMIT_MESSAGE", "record the migration chain")
    config = Config("alembic.ini", toml_file="pyproject.toml")
    script_directory = ScriptDirectory.from_config(config)
    scripts = {script.revision: script for script in script_directory.walk_revisions()}
    corrupt = text(f"UPDATE {HISTORY_TABLE} SET script_sha256 = repeat('0', 64) WHERE revision = :revision")

    command.upgrade(config, "head")

    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            recorded = connection.execute(
                text(
                    "SELECT revision, direction, script_sha256, release_version, applied_at "
                    f"FROM {HISTORY_TABLE} ORDER BY id"
                )
            ).all()

        assert {row.revision for row in recorded} == set(scripts)
        assert {row.direction for row in recorded} == {"upgrade"}
        assert {row.release_version for row in recorded} == {"0123456789abcdef"}
        assert all(row.script_sha256 == script_digest(scripts[row.revision]) for row in recorded)
        # The recorded chain should contain distinct application times, not one release timestamp.
        assert len({row.applied_at for row in recorded}) > 1

        command.downgrade(config, f"{DYNAMIC_VOTING_REVISION}-1")
        with engine.connect() as connection:
            directions = connection.execute(
                text(f"SELECT direction FROM {HISTORY_TABLE} WHERE revision = :revision ORDER BY id"),
                {"revision": DYNAMIC_VOTING_REVISION},
            ).scalars()
            assert list(directions) == ["upgrade", "downgrade"]

        # A revision the database has left contributes no DDL to it, so an edit to that revision
        # is legitimate and must not block the next deploy.
        with engine.begin() as connection:
            connection.execute(corrupt, {"revision": DYNAMIC_VOTING_REVISION})
        command.upgrade(config, "head")

        # A database older than the ledger has applied revisions and no digests at all, which is
        # absence of evidence and must not be read as an edit.
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {HISTORY_TABLE}"))
        command.upgrade(config, "head")

        with engine.begin() as connection:
            assert set(adopt_script_digests(connection, script_directory)) == set(scripts)
        with engine.connect() as connection:
            rows = connection.execute(text(f"SELECT revision, script_sha256 FROM {HISTORY_TABLE}")).all()
        assert {row.revision: row.script_sha256 for row in rows} == {
            revision: script_digest(script) for revision, script in scripts.items()
        }

        head_revision = script_directory.get_current_head()
        assert head_revision is not None
        with engine.begin() as connection:
            connection.execute(corrupt, {"revision": head_revision})
        # A no-op upgrade still verifies, so the guard does not need a pending step to fire.
        with pytest.raises(CommandError, match="have been edited since this database ran them"):
            command.upgrade(config, "head")

        with engine.begin() as connection:
            assert set(repair_script_digests(connection, script_directory)) == {head_revision}
        with engine.connect() as connection:
            assert divergent_revisions(connection, script_directory) == {}
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_schema_audit_constraints(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject invalid categories, aliases, and object keys at the database boundary."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _begin_build_edit(connection)
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            for category in (None, "Unknown"):
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
                            "VALUES (0, :category, :account, false)"
                        ),
                        {"category": category, "account": account},
                    )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO schematic_files (sha256, byte_size, source_format, object_key) "
                        "VALUES (:sha, 1, 'schem', 'wrong/key')"
                    ),
                    {"sha": "a" * 64},
                )
            roles = list(
                connection.execute(
                    text(
                        "INSERT INTO permission_roles (slug, name) VALUES ('audit-aa', 'A'), ('audit-bb', 'B'), "
                        "('audit-cc', 'C') RETURNING id"
                    )
                ).scalars()
            )
            connection.execute(
                text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:a, :b), (:b, :c)"),
                dict(zip(("a", "b", "c"), roles, strict=True)),
            )
            with pytest.raises(DBAPIError, match="inclusion cycle"), connection.begin_nested():
                connection.execute(
                    text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:c, :a)"),
                    {"a": roles[0], "c": roles[2]},
                )
    finally:
        engine.dispose()


def test_schema_audit_backfills_legacy_rows(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve loadable builds and exact aliases while dropping unresolvable legacy votes."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "d5f9a2b7e0c3")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            accounts = [
                connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
                for _ in range(3)
            ]
            build = connection.execute(
                text(
                    "INSERT INTO builds (submission_status, submitter_account_id, ai_generated) "
                    "VALUES (0, :account, false) RETURNING id"
                ),
                {"account": accounts[0]},
            ).scalar_one()
            vote_session = connection.execute(
                text(
                    "INSERT INTO vote_sessions (status, result, author_account_id, kind, pass_threshold, fail_threshold) "
                    "VALUES ('open', 'pending', :account, 'delete_log', 3, -3) RETURNING id"
                ),
                {"account": accounts[0]},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO delete_log_vote_sessions "
                    "(vote_session_id, target_message_id, target_channel_id, target_server_id) VALUES (:session, 1, 2, 3)"
                ),
                {"session": vote_session},
            )
            connection.execute(
                text(
                    "INSERT INTO vote_session_options (vote_session_id, guild_id, emoji, identifier, choice, position) "
                    "VALUES (:session, 0, 'yes', 'approve', 'approve', 0), "
                    "(:session, 42, 'yes', 'approve', 'approve', 0)"
                ),
                {"session": vote_session},
            )
            connection.execute(
                text(
                    "INSERT INTO votes (vote_session_id, account_id, guild_id, option_id, emoji, weight) "
                    "VALUES (:session, :local, 42, 'approve', 'yes', 1), "
                    "(:session, :global, 0, 'approve', 'yes', 1), "
                    "(:session, :missing, 42, 'missing', '?', 1)"
                ),
                {"session": vote_session, "local": accounts[0], "global": accounts[1], "missing": accounts[2]},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT category FROM builds JOIN other_builds ON builds.id = other_builds.build_id WHERE id = :id"
                    ),
                    {"id": build},
                ).scalar_one()
                == "Other"
            )
            ballots = connection.execute(
                text(
                    "SELECT v.account_id, v.guild_id, o.guild_id FROM votes v "
                    "JOIN vote_session_options o ON o.id = v.option_alias_id ORDER BY v.account_id"
                )
            ).all()
            assert ballots == [(accounts[0], 42, 42), (accounts[1], None, None)]
        command.check(config)
    finally:
        engine.dispose()


def test_record_terminology_preserves_identities_and_json(
    migration_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename populated titles and filters in both directions without losing identity or unrelated JSON."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "b9d3e6f1c4a7")
    engine = create_engine(migration_database_url)
    original_filter = {
        "record_classes": ["fastest"],
        "version_scopes": ["current", "all_time"],
        "build_kinds": ["door"],
    }
    original_document = {"record_class": "fastest", "version_scope": "current", "build_kind": "door"}
    try:
        with engine.begin() as connection:
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO notification_subscriptions (account_id, kind, filter) "
                    "VALUES (:account_id, 'record_filter', CAST(:filter AS jsonb))"
                ),
                {"account_id": account_id, "filter": json.dumps(original_filter)},
            )
            title_id = connection.execute(
                text("""
                INSERT INTO record_competitions (record_class, build_kind, version_scope, category_key)
                VALUES ('fastest', 'door', 'current', 'migration-test') RETURNING public_id
            """)
            ).scalar_one()
            document_id = connection.execute(
                text("""
                    INSERT INTO search_documents
                        (resource_kind, source_key, title, normalized_title, fuzzy_text, source_hash, document_data, tags)
                    VALUES ('record', 'result:1', 'Fastest Door', 'fastest door', 'fastest door', 'old-hash',
                        CAST(:document AS jsonb), ARRAY['fastest', 'current']) RETURNING id
                """),
                {"document": json.dumps(original_document)},
            ).scalar_one()
            connection.execute(
                text("""
                    INSERT INTO search_document_facets (document_id, field_name, ordinal, text_value)
                    VALUES (:id, 'record_class', 0, 'fastest'), (:id, 'version_scope', 0, 'current')
                """),
                {"id": document_id},
            )

        command.upgrade(config, "c0e4f7a2d5b8")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT public_id FROM record_titles")).scalar_one() == title_id
            assert connection.execute(text("SELECT version_scope FROM record_titles")).scalar_one() == "up_to_date"
            assert connection.execute(text("SELECT filter FROM notification_subscriptions")).scalar_one() == {
                "base_titles": ["fastest"],
                "version_scopes": ["up_to_date", "all_time"],
                "build_kinds": ["door"],
            }
            assert connection.execute(text("SELECT document_data FROM search_documents")).scalar_one() == {
                "base_title": "fastest",
                "version_scope": "up_to_date",
                "build_kind": "door",
            }
            assert dict(
                connection.execute(text("SELECT field_name, text_value FROM search_document_facets")).all()
            ) == {
                "base_title": "fastest",
                "version_scope": "up_to_date",
            }
            assert connection.execute(text("SELECT tags FROM search_documents")).scalar_one() == [
                "fastest",
                "up_to_date",
            ]

        command.downgrade(config, "b9d3e6f1c4a7")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT public_id FROM record_competitions")).scalar_one() == title_id
            assert connection.execute(text("SELECT version_scope FROM record_competitions")).scalar_one() == "current"
            assert (
                connection.execute(text("SELECT filter FROM notification_subscriptions")).scalar_one()
                == original_filter
            )
            assert (
                connection.execute(text("SELECT document_data FROM search_documents")).scalar_one() == original_document
            )
            assert dict(
                connection.execute(text("SELECT field_name, text_value FROM search_document_facets")).all()
            ) == {
                "record_class": "fastest",
                "version_scope": "current",
            }
            assert connection.execute(text("SELECT tags FROM search_documents")).scalar_one() == ["fastest", "current"]
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


def _insert_build(connection: Connection, account_id: int) -> int:
    build_id = connection.execute(
        text(
            "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
            "VALUES (0, 'Other', :account, false) RETURNING id"
        ),
        {"account": account_id},
    ).scalar_one()
    connection.execute(text("INSERT INTO other_builds (build_id) VALUES (:id)"), {"id": build_id})
    return build_id


def _insert_vote_session(connection: Connection, account_id: int, kind: str) -> int:
    # Generic polls close at their deadline, so they carry no thresholds.
    threshold = None if kind == "generic" else 1
    return connection.execute(
        text(
            "INSERT INTO vote_sessions (status, result, author_account_id, kind, pass_threshold, fail_threshold) "
            "VALUES ('open', 'pending', :author, :kind, :threshold, -CAST(:threshold AS real)) RETURNING id"
        ),
        {"author": account_id, "kind": kind, "threshold": threshold},
    ).scalar_one()


def _begin_build_edit(connection: Connection) -> None:
    connection.execute(
        text(
            "SELECT begin_build_edit(jsonb_build_object("
            "'actor_system', 'migration_test', 'source', 'test', 'operation', 'edit', "
            "'operation_id', gen_random_uuid()))"
        )
    )


def test_deleting_a_build_removes_only_its_vote_sessions(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build delete must not take generic polls or delete-log votes with it."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _begin_build_edit(connection)
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            doomed_build, kept_build = _insert_build(connection, account), _insert_build(connection, account)
            sessions = {}
            for name, build_id in (("doomed", doomed_build), ("kept_build", kept_build)):
                sessions[name] = _insert_vote_session(connection, account, "build")
                connection.execute(
                    text("INSERT INTO build_vote_sessions (vote_session_id, build_id, changes) VALUES (:s, :b, '[]')"),
                    {"s": sessions[name], "b": build_id},
                )
            sessions["generic"] = _insert_vote_session(connection, account, "generic")
            connection.execute(
                text(
                    "INSERT INTO generic_vote_sessions (vote_session_id, question, visibility, deadline) "
                    "VALUES (:s, 'Q?', 'visible_live', now() + interval '1 day')"
                ),
                {"s": sessions["generic"]},
            )
            sessions["delete_log"] = _insert_vote_session(connection, account, "delete_log")
            connection.execute(
                text(
                    "INSERT INTO delete_log_vote_sessions "
                    "(vote_session_id, target_message_id, target_channel_id, target_server_id) VALUES (:s, 1, 2, 3)"
                ),
                {"s": sessions["delete_log"]},
            )

        with engine.begin() as connection:
            _begin_build_edit(connection)
            # The old statement trigger deleted every orphan-looking session even when no build matched.
            connection.execute(text("DELETE FROM builds WHERE id = -1"))
            connection.execute(text("DELETE FROM builds WHERE id = :id"), {"id": doomed_build})

        with engine.connect() as connection:
            remaining = set(connection.execute(text("SELECT id FROM vote_sessions")).scalars())
        assert remaining == {sessions["kept_build"], sessions["generic"], sessions["delete_log"]}
    finally:
        engine.dispose()


def test_role_includes_stay_within_one_guild(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role may include global roles and roles of its own guild, but no other guild's roles."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            ids = dict(
                connection.execute(
                    text(
                        "INSERT INTO permission_roles (slug, name, guild_id) VALUES "
                        "('global-a', 'A', NULL), ('global-b', 'B', NULL), "
                        "('guild-one', 'One', 1), ('guild-one-b', 'One B', 1), ('guild-two', 'Two', 2) "
                        "RETURNING slug, id"
                    )
                ).all()
            )
            include = text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:role, :inc)")
            for role, included in (("global-a", "global-b"), ("guild-one", "global-a"), ("guild-one", "guild-one-b")):
                connection.execute(include, {"role": ids[role], "inc": ids[included]})
            for role, included in (("global-b", "guild-one"), ("guild-one", "guild-two")):
                with pytest.raises(DBAPIError, match="own guild"), connection.begin_nested():
                    connection.execute(include, {"role": ids[role], "inc": ids[included]})
    finally:
        engine.dispose()


def test_build_lease_and_embedding_writes_queue_nothing(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a visible change to a build may queue search, recompute, or Discord work."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(migration_database_url)
    queues = ("search_projection_queue", "record_recompute_queue", "discord_sync_queue")
    try:
        with engine.begin() as connection:
            _begin_build_edit(connection)
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            build_id = _insert_build(connection, account)
            for queue in queues:
                connection.execute(text(f"DELETE FROM {queue}"))
            connection.execute(
                text(
                    "UPDATE builds SET locked_at = now(), lock_token = gen_random_uuid(), "
                    "lock_expires_at = now() + interval '5 minutes' WHERE id = :id"
                ),
                {"id": build_id},
            )
            connection.execute(
                text("UPDATE builds SET locked_at = NULL, lock_token = NULL, lock_expires_at = NULL WHERE id = :id"),
                {"id": build_id},
            )
            assert all(connection.execute(text(f"SELECT count(*) FROM {queue}")).scalar_one() == 0 for queue in queues)

            connection.execute(text("UPDATE builds SET description = 'Changed' WHERE id = :id"), {"id": build_id})
            assert connection.execute(text("SELECT count(*) FROM search_projection_queue")).scalar_one() > 0
            assert connection.execute(text("SELECT count(*) FROM discord_sync_queue")).scalar_one() > 0
    finally:
        engine.dispose()


def test_hardening_constraints(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject builds without their category's subtype row, duplicate versions, and duplicate valid codes."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
        insert_build = text(
            "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
            "VALUES (0, 'Door', :account, false) RETURNING id"
        )
        # The subtype check is deferred, so it fires at commit.
        for subtype in (None, "other_builds"):
            connection = engine.connect()
            try:
                _begin_build_edit(connection)
                build_id = connection.execute(insert_build, {"account": account}).scalar_one()
                if subtype is not None:
                    connection.execute(text(f"INSERT INTO {subtype} (build_id) VALUES (:id)"), {"id": build_id})
                with pytest.raises(DBAPIError, match="subtype counts"):
                    connection.commit()
            finally:
                connection.close()

        with engine.begin() as connection:
            insert_version = text(
                "INSERT INTO versions (edition, major_version, minor_version, patch_number) VALUES ('Java', 99, 0, 0)"
            )
            connection.execute(insert_version)
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(insert_version)
            for edition, major in (("Lego", 1), ("Java", -1)):
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO versions (edition, major_version, minor_version, patch_number) "
                            "VALUES (:edition, :major, 0, 0)"
                        ),
                        {"edition": edition, "major": major},
                    )

            insert_code = text(
                "INSERT INTO verification_codes (minecraft_uuid, code, username, valid) "
                "VALUES (gen_random_uuid(), 'same', 'Player', :valid)"
            )
            connection.execute(insert_code, {"valid": True})
            connection.execute(insert_code, {"valid": False})
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(insert_code, {"valid": True})
    finally:
        engine.dispose()


def test_build_notes_move_into_description_without_requeueing_unchanged_cards(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge extra_info.user into description, and queue Discord work only where the review card changes."""
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "f3c6b9e4a1d7")
    engine = create_engine(migration_database_url)
    queues = ("search_projection_queue", "discord_sync_queue")
    candidates = [
        {"description": None, "extra_info": {"user": "inferred note", "unknown_patterns": ["Odd"]}},
        {"description": "kept", "extra_info": {}},
    ]
    try:
        with engine.begin() as connection:
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            # The note only in extra_info, the same note in both places, and two notes that disagree.
            only_user, same, conflict = (_insert_build(connection, account) for _ in range(3))
            notes = {only_user: (None, "legacy"), same: ("same", "same"), conflict: ("current", "stale")}
            for build_id, (description, user) in notes.items():
                connection.execute(
                    text(
                        "UPDATE builds SET description = :description, "
                        "extra_info = jsonb_build_object('user', CAST(:user AS text), 'server_info', '{}'::jsonb) "
                        "WHERE id = :id"
                    ),
                    {"id": build_id, "description": description, "user": user},
                )
            connection.execute(
                text(
                    "INSERT INTO submission_inference_runs (id, owner_account_id, inputs, state, expires_at, candidates) "
                    "VALUES (gen_random_uuid(), :account, '{}'::jsonb, 'completed', now() + interval '1 day', "
                    "CAST(:candidates AS jsonb))"
                ),
                {"account": account, "candidates": json.dumps(candidates)},
            )
            for queue in queues:
                connection.execute(text(f"DELETE FROM {queue}"))

        command.upgrade(config, "a7d2c5e8f1b3")
        with engine.connect() as connection:
            rows = {
                build_id: (description, extra_info)
                for build_id, description, extra_info in connection.execute(
                    text("SELECT id, description, extra_info FROM builds")
                )
            }
            assert {build_id: rows[build_id] for build_id in notes} == {
                only_user: ("legacy", {"server_info": {}}),
                same: ("same", {"server_info": {}}),
                conflict: ("current", {"server_info": {}}),
            }
            assert connection.execute(text("SELECT candidates FROM submission_inference_runs")).scalar_one() == [
                {"description": "inferred note", "extra_info": {"unknown_patterns": ["Odd"]}},
                {"description": "kept", "extra_info": {}},
            ]
            queued = {
                queue: set(
                    connection.execute(text(f"SELECT source_key FROM {queue} WHERE resource_kind = 'build'")).scalars()
                )
                for queue in queues
            }
            assert queued == {"search_projection_queue": {str(conflict)}, "discord_sync_queue": {str(conflict)}}
            disabled = connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.builds'::regclass "
                    "AND tgname LIKE 'builds_enqueue_%' AND tgenabled = 'D'"
                )
            ).scalar_one()
            assert disabled == 0

        command.downgrade(config, "f3c6b9e4a1d7")
        with engine.connect() as connection:
            restored = dict(connection.execute(text("SELECT id, extra_info->>'user' FROM builds")).tuples().all())
            assert {build_id: restored[build_id] for build_id in notes} == {
                only_user: "legacy",
                same: "same",
                conflict: "current",
            }
            assert connection.execute(text("SELECT candidates FROM submission_inference_runs")).scalar_one() == [
                {"description": "inferred note", "extra_info": {"user": "inferred note", "unknown_patterns": ["Odd"]}},
                {"description": "kept", "extra_info": {"user": "kept"}},
            ]
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_starboard_message_migration_preserves_deletions_and_content(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    revision = "c9f4e7a2b5d8"
    command.upgrade(config, f"{revision}-1")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO server_settings (server_id) VALUES (10)"))
            connection.execute(
                text("""
                INSERT INTO messages (id, guild_id, channel_id, author_id, content, created_at, deleted_at)
                VALUES (100, 10, 20, 30, 'retained', now(), now()),
                       (101, 10, 20, 30, 'also retained', now(), NULL)
            """)
            )
            connection.execute(
                text("""
                INSERT INTO starboard_origin_messages
                    (id, guild_id, channel_id, author_id, author_is_bot, posted_at, deleted_at)
                VALUES (100, 10, 20, 30, false, now(), NULL),
                       (101, 10, 20, 30, false, now(), now()),
                       (102, 10, 20, 30, false, now(), NULL)
            """)
            )
        command.upgrade(config, revision)
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, content, deleted_at IS NOT NULL FROM messages ORDER BY id")
            ).all()
            assert rows == [(100, "retained", True), (101, "also retained", True), (102, None, False)]
        command.downgrade(config, f"{revision}-1")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT id, deleted_at IS NOT NULL FROM starboard_origin_messages ORDER BY id")
            ).all() == [(100, True), (101, True), (102, False)]
        command.upgrade(config, revision)
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


def test_draft_schematic_migration_recovers_waiting_uploads(
    migration_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "b2c7d0e5f8a1")
    draft_id, source_id = uuid4(), uuid4()
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            owner = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            connection.execute(
                text("""
                INSERT INTO submission_drafts
                  (id, owner_account_id, schema_id, schema_revision, category, status, answers,
                   origin, expires_at, preparation_issues)
                VALUES (:draft, :owner, 'build_submission.v1', 2, 'other', 'needs_attention', '{}',
                        'discord', now() + interval '7 days',
                        '[{"field_id":"schematic","reason":"schematic_processing"}]')
            """),
                {"draft": draft_id, "owner": owner},
            )
            connection.execute(
                text("""
                INSERT INTO submission_schematic_sources
                  (id, owner_account_id, uploaded_by_account_id, filename, sha256, byte_size, state)
                VALUES (:source, :owner, :owner, 'original.schem', :sha, 3, 'waiting_sanitizer')
            """),
                {"source": source_id, "owner": owner, "sha": "a" * 64},
            )
            connection.execute(
                text("""
                INSERT INTO submission_draft_schematics (draft_id, source_id, is_primary)
                VALUES (:draft, :source, true)
            """),
                {"draft": draft_id, "source": source_id},
            )
        command.upgrade(config, "c9d2e5f8a1b4")
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT state, sha256, analysis FROM submission_schematic_sources WHERE id = :id"),
                {"id": source_id},
            ).one()
            assert tuple(row) == ("pending", "a" * 64, None)
            assert connection.execute(
                text("SELECT preparation_retry_at IS NOT NULL FROM submission_drafts WHERE id = :id"), {"id": draft_id}
            ).scalar_one()
        command.downgrade(config, "b2c7d0e5f8a1")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT state FROM submission_schematic_sources WHERE id = :id"), {"id": source_id}
                ).scalar_one()
                == "waiting_sanitizer"
            )
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_extender_timings_move_out_of_submission_provenance_without_history(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, "a5b8c1d4e7f0")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _begin_build_edit(connection)
            account = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            builds = {}
            for name, timing in (("submitted", {"extension": 7, "retraction": 9}), ("legacy", None)):
                builds[name] = connection.execute(
                    text(
                        "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated, "
                        "extra_info) VALUES (0, 'Extender', :account, false, CAST(:info AS jsonb)) RETURNING id"
                    ),
                    {
                        "account": account,
                        "info": json.dumps({"submission_provenance": {"timing": timing}} if timing else {}),
                    },
                ).scalar_one()
                connection.execute(text("INSERT INTO extenders (build_id) VALUES (:id)"), {"id": builds[name]})
        with engine.connect() as connection:
            history_before = connection.execute(text("SELECT count(*) FROM build_edit_history")).scalar_one()

        command.upgrade(config, "4fd493faba21")
        with engine.connect() as connection:
            timings = {
                build_id: (extension, retraction)
                for build_id, extension, retraction in connection.execute(
                    text("SELECT build_id, extension_time, retraction_time FROM extenders")
                )
            }
            assert timings == {builds["submitted"]: (7, 9), builds["legacy"]: (None, None)}
            assert connection.execute(text("SELECT count(*) FROM build_edit_history")).scalar_one() == history_before
            snapshot = connection.execute(
                text("SELECT build_history_snapshot_v1(:id)"), {"id": builds["submitted"]}
            ).scalar_one()
            assert snapshot["extenders"]["extension_time"] == 7

        command.downgrade(config, "a5b8c1d4e7f0")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
