"""PostgreSQL coverage for provider-neutral synchronized build targets."""

import uuid
from collections.abc import AsyncIterator

import psycopg2
import pytest
from alembic.config import Config
from psycopg2 import sql
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from squid.accounts.infrastructure.models import Account
from squid.accounts.infrastructure.repository import AccountRepository
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.repository import BuildRepository
from squid.versions.infrastructure.models import Version


@pytest.fixture
async def migrated_session_factory(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated database at Alembic head for build repository tests."""
    database_name = f"redstone_squid_build_targets_{uuid.uuid4().hex}"
    admin_url = postgres_container.get_connection_url(driver="psycopg2")
    admin_dsn = admin_url.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    finally:
        connection.close()

    migration_url = admin_url.rsplit("/", maxsplit=1)[0] + f"/{database_name}"
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_async_engine(migration_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        connection = psycopg2.connect(admin_dsn)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        finally:
            connection.close()


async def _seed_account_and_version(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory.begin() as session:
        account = Account()
        session.add_all(
            [
                account,
                Version(
                    edition="Java",
                    major_version=1,
                    minor_version=21,
                    patch_number=0,
                    data_version=3953,
                ),
            ]
        )
        await session.flush()
        return account.id


def _build(category: BuildCategory, account_id: int, *, draft_id: uuid.UUID | None = None) -> Build:
    build = Build(
        category=category,
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        source_submission_draft_id=draft_id,
        display_name="Workshop prototype" if draft_id is not None else None,
        versions=["Java 1.21.0"],
        width=3,
        height=4,
        depth=5,
    )
    if category is BuildCategory.DOOR:
        build.door_width = 2
        build.door_height = 3
        build.door_orientation_type = "Door"
    elif category is BuildCategory.EXTENDER:
        build.extender_orientation = "Upward"
        build.extension_length = 3
        build.extender_type = "Regular"
    return build


async def test_repository_round_trips_and_updates_every_manifest_category(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    draft_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    builds = [
        _build(category, account_id, draft_id=draft_id if category is BuildCategory.OTHER else None)
        for category in BuildCategory
    ]

    for build in builds:
        await repository.save(build)
        assert build.id is not None

    loaded = [await repository.get_by_id(build.id) for build in builds if build.id is not None]
    assert [build.category if build is not None else None for build in loaded] == list(BuildCategory)
    assert all(build is not None and build.submitter_account_id == account_id for build in loaded)
    assert all(build is not None and build.submitter_id is None for build in loaded)
    other = await repository.get_by_source_submission_draft_id(draft_id)
    assert other is not None
    assert other.category is BuildCategory.OTHER
    assert other.display_name == "Workshop prototype"
    pending = await repository.get_pending()
    assert {build.category for build in pending} == set(BuildCategory)
    account_page = await repository.list_page(
        statuses=frozenset({Status.PENDING}),
        submitter_id=None,
        submitter_account_id=account_id,
        after_id=None,
        limit=10,
    )
    assert {build.category for build in account_page} == set(BuildCategory)

    for build in loaded:
        assert build is not None
        assert build.id is not None
        assert build.category is not None
        category = build.category
        build.description = f"Updated {category.value}"
        await repository.save(build)
        reloaded = await repository.get_by_id(build.id)
        assert reloaded is not None
        assert reloaded.description == f"Updated {category.value}"
        assert reloaded.revision == 2

    duplicate = _build(BuildCategory.OTHER, account_id, draft_id=draft_id)
    with pytest.raises(IntegrityError):
        await repository.save(duplicate)


async def test_account_merge_transfers_drafts_schematic_rights_and_build_ownership(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    draft_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    async with migrated_session_factory.begin() as session:
        build_id = (
            await session.execute(
                text(
                    "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
                    "VALUES (0, 'Utility', :absorbed, false) RETURNING id"
                ),
                {"absorbed": absorbed.id},
            )
        ).scalar_one()
        await session.execute(text("INSERT INTO utilities (build_id) VALUES (:build_id)"), {"build_id": build_id})
        await session.execute(
            text(
                "INSERT INTO submission_drafts "
                "(id, owner_account_id, schema_id, schema_revision, category, answers, origin, expires_at) "
                "VALUES (:draft_id, :absorbed, 'redstone_squid.submission', 1, 'utility', '{}'::jsonb, "
                "'web', now() + interval '7 days')"
            ),
            {"draft_id": draft_id, "absorbed": absorbed.id},
        )
        await session.execute(
            text(
                "INSERT INTO submission_draft_access (draft_id, account_id, role) VALUES "
                "(:draft_id, :absorbed, 'owner'), (:draft_id, :survivor, 'editor')"
            ),
            {"draft_id": draft_id, "absorbed": absorbed.id, "survivor": survivor.id},
        )
        await session.execute(
            text(
                "INSERT INTO submission_draft_changes "
                "(draft_id, actor_account_id, base_revision, resulting_revision, client_instance_id, "
                "idempotency_key, operations) VALUES "
                "(:draft_id, :absorbed, 0, 1, 'web-test', 'merge-test-key', '[{\"op\": \"set\"}]'::jsonb)"
            ),
            {"draft_id": draft_id, "absorbed": absorbed.id},
        )
        await session.execute(
            text(
                "INSERT INTO schematic_files (sha256, byte_size, source_format, data) "
                "VALUES ('merge-test-sha', 1, 'schem', decode('00', 'hex'))"
            )
        )
        await session.execute(
            text(
                "INSERT INTO build_schematics "
                "(build_id, file_sha256, is_primary, width, height, length, allocated_width, allocated_height, "
                "allocated_length, block_count, bounding_volume, entity_count, palette_size, region_names, signs, "
                "analyzer_version, analysis_schema_version, visibility, rights_attested_at, "
                "rights_attested_by_account_id) VALUES "
                "(:build_id, 'merge-test-sha', true, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, ARRAY[]::text[], "
                "'[]'::jsonb, 'test-1', 1, 'reviewer_only', now(), :absorbed)"
            ),
            {"build_id": build_id, "absorbed": absorbed.id},
        )

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        build_owner = await session.scalar(select(SQLBuild.submitter_account_id).where(SQLBuild.id == build_id))
        draft_owner = await session.scalar(
            text("SELECT owner_account_id FROM submission_drafts WHERE id = :draft_id"),
            {"draft_id": draft_id},
        )
        access = (
            await session.execute(
                text("SELECT account_id, role FROM submission_draft_access WHERE draft_id = :draft_id"),
                {"draft_id": draft_id},
            )
        ).all()
        change_actor = await session.scalar(
            text("SELECT actor_account_id FROM submission_draft_changes WHERE draft_id = :draft_id"),
            {"draft_id": draft_id},
        )
        rights_actor = await session.scalar(
            text("SELECT rights_attested_by_account_id FROM build_schematics WHERE build_id = :build_id"),
            {"build_id": build_id},
        )

    assert build_owner == survivor.id
    assert draft_owner == survivor.id
    assert access == [(survivor.id, "owner")]
    assert change_actor == survivor.id
    assert rights_actor == survivor.id
    assert await accounts.get_by_id(absorbed.id) is None
