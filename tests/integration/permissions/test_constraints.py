"""The permission CHECK constraints, exercised against a real database.

These are the guards that survive a bug in the service layer, so they are worth
proving rather than assuming: the anti-escalation rule in particular is enforced
in three places, and this is the only one an application mistake cannot bypass.
"""

from collections.abc import Iterator

import psycopg2
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command

PERMISSION_DATABASE = "redstone_squid_permissions"


@pytest.fixture
def permission_database_url(postgres_container: PostgresContainer) -> Iterator[str]:
    """An isolated database with the full migration chain applied."""
    admin_url = postgres_container.get_connection_url(driver="psycopg2")
    admin_dsn = admin_url.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{PERMISSION_DATABASE}"')
    finally:
        connection.close()

    url = admin_url.rsplit("/", maxsplit=1)[0] + f"/{PERMISSION_DATABASE}"
    try:
        yield url
    finally:
        connection = psycopg2.connect(admin_dsn)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (PERMISSION_DATABASE,),
                )
                cursor.execute(f'DROP DATABASE "{PERMISSION_DATABASE}"')
        finally:
            connection.close()


@pytest.fixture
def migrated_engine(permission_database_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SQUID_DATABASE_URL", permission_database_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_engine(permission_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _account(connection) -> int:
    return connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()


def test_a_role_from_one_guild_cannot_carry_authority_into_another(migrated_engine) -> None:
    """The escalation this whole design exists to prevent, refused in storage."""
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="role_subject_stays_home"):
        connection.execute(
            text(
                "INSERT INTO permission_grants (pattern, effect, subject_role_id, subject_guild_id, scope_guild_id) "
                "VALUES ('settings.server.edit', 1, 42, 100, 200)"
            )
        )


def test_a_role_subject_without_its_guild_is_refused(migrated_engine) -> None:
    """A role snowflake means nothing without the guild it lives in, and the
    anti-escalation CHECK compares against that column."""
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="role_subject_has_guild"):
        connection.execute(
            text("INSERT INTO permission_grants (pattern, effect, subject_role_id) VALUES ('settings.**', 1, 42)")
        )


def test_a_rule_needs_exactly_one_subject(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        account_id = _account(connection)
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="one_subject"):
        connection.execute(
            text(
                "INSERT INTO permission_grants (pattern, effect, subject_account_id, subject_role_id, subject_guild_id)"
                " VALUES ('settings.**', 1, :account_id, 42, 100)"
            ),
            {"account_id": account_id},
        )


def test_a_duplicate_rule_is_refused(migrated_engine) -> None:
    """`NULLS NOT DISTINCT`, so two global rules for one subject collide."""
    with migrated_engine.begin() as connection:
        account_id = _account(connection)
        connection.execute(
            text("INSERT INTO permission_grants (pattern, effect, subject_account_id) VALUES ('build.**', 1, :id)"),
            {"id": account_id},
        )
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="permission_grants_account_unique"):
        connection.execute(
            text("INSERT INTO permission_grants (pattern, effect, subject_account_id) VALUES ('build.**', -1, :id)"),
            {"id": account_id},
        )


def test_a_self_including_role_is_refused(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM permission_roles WHERE builtin_key = 'trusted'")).scalar_one()
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="no_self_include"):
        connection.execute(
            text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:id, :id)"),
            {"id": role_id},
        )


def test_an_unknown_effect_is_refused(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        account_id = _account(connection)
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="effect_check"):
        connection.execute(
            text("INSERT INTO permission_grants (pattern, effect, subject_account_id) VALUES ('build.**', 5, :id)"),
            {"id": account_id},
        )


def test_a_builtin_role_cannot_be_scoped_to_a_guild(migrated_engine) -> None:
    with migrated_engine.begin() as connection, pytest.raises(DBAPIError, match="builtin_is_global"):
        connection.execute(
            text(
                "INSERT INTO permission_roles (slug, name, guild_id, builtin_key) "
                "VALUES ('rogue', 'Rogue', 100, 'rogue')"
            )
        )


def test_the_builtin_roles_are_seeded_without_patterns(migrated_engine) -> None:
    """Their pattern lists live in code; a seeded copy would freeze the catalogue."""
    with migrated_engine.connect() as connection:
        slugs = (
            connection.execute(
                text("SELECT slug FROM permission_roles WHERE builtin_key IS NOT NULL ORDER BY rank DESC")
            )
            .scalars()
            .all()
        )
        patterns = connection.execute(text("SELECT count(*) FROM permission_role_patterns")).scalar_one()

    assert slugs == ["owner", "global-admin", "guild-admin", "trusted"]
    assert patterns == 0
