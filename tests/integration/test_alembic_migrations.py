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
from squid.persistence.alembic_entities import alembic_util_entities
from squid.worker.queue_health import QUEUE_HEALTH_STATEMENT

MIGRATION_DATABASE = "redstone_squid_migrations"
DYNAMIC_VOTING_REVISION = "4c9e7a2b1d63"
"""The migration whose downgrade path the drift test also exercises."""

NULLABLE_VOTE_THRESHOLDS_REVISION = "b2c3d4e5f6a8"
VOTE_SUBTYPE_INVARIANT_REVISION = "a9b5c8d3e6f0"
IDEMPOTENCY_CALLER_REVISION = "b0c6d9e4f7a1"
FINALIZATION_RESULT_METADATA_REVISION = "c1d7e0f5a8b2"

pytestmark = pytest.mark.filterwarnings("ignore:Expression #.* detected to include an operator clause:UserWarning")
"""Autogenerate cannot compare an index expression carrying an operator class.

`search_document_facets_text_prefix_idx` declares `text_pattern_ops` inline because
`postgresql_ops` is keyed by column name and is silently dropped for an expression;
`squid/search/infrastructure/models.py` documents the choice and notes that Alembic
skipping the comparison is correct, since the migration creates exactly that index.
The suite promotes warnings to errors, so without this every command that autogenerates
fails on an outcome the schema deliberately accepts.
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
    """A clean PostgreSQL database reaches head with all managed entities in sync.

    The dynamic-voting downgrade is exercised here rather than in a test of its own on
    purpose: reaching a downgradable state costs a full run of the chain, and a downgrade
    is the one path that never runs in production, so it is the one most likely to be
    wrong. Fusing the two pays the setup cost once.
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
                    "INSERT INTO discord_sync_queue (resource_kind, source_key, claimed_at) "
                    "VALUES ('vote_session', 'expired-health-claim', now() - interval '6 minutes')"
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
        entity.signature.partition("(")[0]
        for entity in alembic_util_entities()
        if type(entity).__name__ == "PGFunction"
    }
    expected_triggers = {entity.signature for entity in alembic_util_entities() if type(entity).__name__ == "PGTrigger"}
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
    # Generations come from a shared sequence, so assert the relationships rather than
    # literal values. A post recorded below the queued generation reads as stale, and
    # re-enqueueing keeps the generation rather than restarting it.
    assert stale_post is True
    assert reissued_generation == seeded_generation


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


def test_vote_subtype_invariant_upgrade_rejects_existing_incoherence(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rollout refuses a root that has no matching subtype payload."""
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
    """Build-only writers coexist with legacy metadata writers during the drain window."""
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
            account_id = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
            build_id = connection.execute(
                text(
                    "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated, "
                    "sponsor_installation_id, sponsor_display_name) "
                    "VALUES (0, 'Utility', :account_id, false, :installation_id, 'Example server') RETURNING id"
                ),
                {"account_id": account_id, "installation_id": installation_id},
            ).scalar_one()

        with pytest.raises(DBAPIError, match="cannot downgrade while sponsor attribution schema data is retained"):
            command.downgrade(config, "f4a5b6c7d8e9")

        with engine.begin() as connection:
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
                    "'{\"payload_schema\": 2}'::jsonb, 'needs_attention', now(), "
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
    """Schema-one requests cannot cross the migration without a verified sponsor snapshot."""
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
                    '\'{"payload_schema": 1, "sponsor_attribution": true}\'::jsonb, '
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
                    '\'{"payload_schema": "1", "sponsor_attribution": "true"}\'::jsonb, '
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
                    CREATE OR REPLACE FUNCTION public.set_locked_at()
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
