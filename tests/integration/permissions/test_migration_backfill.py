"""Nobody loses access when the tiers become role assignments.

The equivalence table below is the point: for each (subject, node) pair the new
resolver has to answer what the old predicate answered, so the cut-over is a
change of mechanism rather than a change of who can do what.
"""

from collections.abc import AsyncGenerator, Iterator

import psycopg2
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from squid.permissions.application import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.infrastructure.repository import PermissionRepository

BACKFILL_DATABASE = "redstone_squid_backfill"
TABLES_REVISION = "d8e9f0a1b2c3"
BACKFILL_REVISION = "e9f0a1b2c3d4"

GUILD = 900
TRUSTED_ROLE = 901
OTHER_ROLE = 902


@pytest.fixture
def backfill_database_url(postgres_container: PostgresContainer) -> Iterator[str]:
    admin_url = postgres_container.get_connection_url(driver="psycopg2")
    admin_dsn = admin_url.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{BACKFILL_DATABASE}"')
    finally:
        connection.close()

    url = admin_url.rsplit("/", maxsplit=1)[0] + f"/{BACKFILL_DATABASE}"
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
                    (BACKFILL_DATABASE,),
                )
                cursor.execute(f'DROP DATABASE "{BACKFILL_DATABASE}"')
        finally:
            connection.close()


@pytest.fixture
async def backfilled(
    backfill_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[async_sessionmaker, dict[str, int]]]:
    """Seed the legacy tiers at the pre-backfill head, then migrate over them."""
    monkeypatch.setenv("SQUID_DATABASE_URL", backfill_database_url)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.upgrade(config, TABLES_REVISION)

    engine = create_engine(backfill_database_url)
    with engine.begin() as connection:
        administrator = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
        ordinary = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
        trusted_member = connection.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id")).scalar_one()
        connection.execute(
            text(
                "INSERT INTO global_administrators (account_id, granted_by_account_id, granted_at) "
                "VALUES (:account_id, :granted_by, '2026-01-01T00:00:00Z')"
            ),
            {"account_id": administrator, "granted_by": ordinary},
        )
        connection.execute(
            text("INSERT INTO server_settings (server_id, trusted_roles_ids) VALUES (:guild, ARRAY[:role]::bigint[])"),
            {"guild": GUILD, "role": TRUSTED_ROLE},
        )
        connection.execute(
            text(
                "INSERT INTO api_keys (key_id, secret_hash, label, scopes, owner_account_id) "
                "VALUES ('legacy', '\\x00'::bytea, 'legacy', ARRAY['builds:read','votes:cast'], :owner)"
            ),
            {"owner": administrator},
        )
    engine.dispose()

    command.upgrade(config, BACKFILL_REVISION)

    async_engine = create_async_engine(backfill_database_url.replace("psycopg2", "asyncpg"))
    accounts = {"administrator": administrator, "ordinary": ordinary, "trusted_member": trusted_member}
    try:
        yield async_sessionmaker(async_engine, expire_on_commit=False), accounts
    finally:
        await async_engine.dispose()


def _subject(accounts: dict[str, int], who: str) -> Subject:
    """The subject each legacy tier used to describe."""
    if who == "trusted_member":
        return Subject(
            account_id=accounts[who],
            discord_role_ids=frozenset({TRUSTED_ROLE}),
            guild_id=GUILD,
        )
    return Subject(account_id=accounts[who], discord_role_ids=frozenset({OTHER_ROLE}), guild_id=GUILD)


@pytest.mark.parametrize(
    ("who", "node", "expected"),
    [
        # The old `check_is_global_admin` tier.
        ("administrator", "build.submission.approve", True),
        ("administrator", "build.submission.reject", True),
        ("administrator", "tag.proposal.approve", True),
        ("administrator", "account.claim.list", True),
        ("administrator", "version.entry.create", True),
        ("administrator", "restriction.alias.create", True),
        # ...which stopped short of the destructive and permission-granting surface.
        ("administrator", "record.entry.rebuild", False),
        ("administrator", "bot.tree.sync", False),
        ("administrator", "perm.grant.global", False),
        # The old `check_is_trusted_or_global_admin` tier.
        ("trusted_member", "build.schematic.measure_timing", True),
        ("trusted_member", "build.schematic.detect_lattice", True),
        ("trusted_member", "vote.log_delete.cast", True),
        ("trusted_member", "vote.weight.staff", True),
        ("trusted_member", "build.submission.approve", False),
        ("trusted_member", "settings.server.edit", False),
        ("trusted_member", "record.entry.inspect", False),
        # Everyone else.
        ("ordinary", "build.schematic.measure_timing", False),
        ("ordinary", "vote.log_delete.cast", False),
        ("ordinary", "settings.server.edit", False),
        # Public reads stay public for all three.
        ("ordinary", "build.submission.read", True),
        ("trusted_member", "build.submission.read", True),
    ],
)
async def test_the_new_resolver_matches_the_old_tiers(
    backfilled: tuple[async_sessionmaker, dict[str, int]],
    who: str,
    node: str,
    expected: bool,
) -> None:
    session_factory, accounts = backfilled
    permissions = PermissionService(PermissionRepository(session_factory))

    assert await permissions.allows(_subject(accounts, who), node) is expected


async def test_the_original_grant_provenance_survives(
    backfilled: tuple[async_sessionmaker, dict[str, int]],
) -> None:
    """A backfill that forgets who granted what destroys the audit trail it inherits."""
    session_factory, accounts = backfilled

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT granted_by_account_id, granted_at, reason FROM permission_role_assignments "
                    "WHERE subject_account_id = :account_id"
                ),
                {"account_id": accounts["administrator"]},
            )
        ).one()

    assert row.granted_by_account_id == accounts["ordinary"]
    assert row.granted_at.year == 2026
    assert row.reason == "Backfilled from global_administrators"


async def test_api_key_scopes_become_node_patterns(
    backfilled: tuple[async_sessionmaker, dict[str, int]],
) -> None:
    session_factory, _accounts = backfilled

    async with session_factory() as session:
        scopes = (await session.execute(text("SELECT scopes FROM api_keys WHERE key_id = 'legacy'"))).scalar_one()

    assert sorted(scopes) == ["build.submission.read", "vote.poll.cast"]
