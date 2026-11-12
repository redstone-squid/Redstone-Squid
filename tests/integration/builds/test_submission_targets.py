"""PostgreSQL coverage for account-keyed synchronized build targets."""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from typing import override

import anyio
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.models import Account, AccountIdentity
from squid.accounts.models import AccountIdentity as AccountIdentityValue
from squid.builds.application import BuildService
from squid.builds.application.locks import BuildLockService
from squid.builds.application.restrictions import RestrictionService
from squid.builds.application.taxonomy_service import BuildTaxonomyService
from squid.builds.domain import BuildCategory, Status
from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.models import Build, BuildLink, DoorBuild
from squid.core.errors import DataIntegrityError
from squid.events.models import DomainEvent
from squid.minecraft_auth.domain.models import MinecraftClientOrigin
from squid.minecraft_auth.models import PaperInstallation, PlayerAuthorizationChallenge, PlayerGrant
from squid.notifications.models import (
    InboxNotification,
    NotificationDeliveryRecord,
    NotificationProfile,
)
from squid.permissions.domain import BuiltinRoleKeys
from squid.permissions.models import (
    PermissionAuditEntry,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRolePattern,
)
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.models import BuildSchematic, SchematicFile
from squid.sponsors import PublicSponsor
from squid.submissions.application import ClaimedFinalizationJob, StoredDraft
from squid.submissions.application.build_target import SubmissionBuildPreparation
from squid.submissions.application.commit import BuildSubmissionExecutor
from squid.submissions.domain import (
    BuildSubmissionRejected,
    DoorSubmissionDetails,
    DraftSnapshot,
    DraftStatus,
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
)
from squid.submissions.infrastructure.finalization_repository import (
    PostgresFinalizationJobRepository,
)
from squid.submissions.models.entities import SubmissionDraft, SubmissionDraftAccess, SubmissionDraftChange
from squid.submissions.models.finalization import SubmissionFinalizationInput, SubmissionFinalizationJob
from squid.submissions.payload_integrity import submission_payload_digest
from squid.tags.models import TagDefinition
from squid.versions.application import VersionService
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact
from tests.support.submission_targets import normalized_submission, seed_account_and_version, submission_build


class NoopEmbeddings:
    async def prepare(self, build: Build) -> None:
        del build

    async def index(self, build: Build) -> None:
        del build


class NoApprovedTags:
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return ()


class PublicSummaryOnlyBuildRepository(BuildRepository):
    """Fail if the public read model falls back to aggregate hydration."""

    @override
    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        del build_ids
        raise AssertionError("public summaries must not hydrate private build aggregates")


async def test_repository_round_trips_and_updates_every_manifest_category(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    draft_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    builds = [
        submission_build(category, account_id, draft_id=draft_id if category is BuildCategory.OTHER else None)
        for category in BuildCategory
    ]

    for build in builds:
        await transact(
            repository,
            lambda uow, build=build: uow.builds.save(build),
            commit=True,
        )
        assert build.id is not None
        assert build.revision == 1

    loaded = [
        await transact(repository, lambda uow, build=build: uow.builds.get_by_id(build.id))
        for build in builds
        if build.id is not None
    ]
    assert [build.category if build is not None else None for build in loaded] == list(BuildCategory)
    assert all(build is not None and build.submitter_account_id == account_id for build in loaded)
    assert all(build is not None and build.submitter_discord_id is None for build in loaded)
    other = await transact(repository, lambda uow: uow.builds.get_by_source_submission_draft_id(draft_id))
    assert other is not None
    assert other.category is BuildCategory.OTHER
    assert other.display_name == "Workshop prototype"
    pending = await transact(repository, lambda uow: uow.builds.get_pending())
    assert {build.category for build in pending} == set(BuildCategory)
    account_page = await transact(
        repository,
        lambda uow: uow.builds.list_page(
            statuses=frozenset({Status.PENDING}), submitter_account_id=account_id, after_id=None, limit=10
        ),
    )
    assert {build.category for build in account_page} == set(BuildCategory)

    for build in loaded:
        assert build is not None
        assert build.id is not None
        assert build.category is not None
        category = build.category
        build.description = f"Updated {category.value}"
        await transact(
            repository,
            lambda uow, build=build: uow.builds.save(build),
            commit=True,
        )
        reloaded = await transact(repository, lambda uow, build=build: uow.builds.get_by_id(build.id))
        assert reloaded is not None
        assert reloaded.description == f"Updated {category.value}"
        assert reloaded.revision == 2

    duplicate = submission_build(BuildCategory.OTHER, account_id, draft_id=draft_id)
    with pytest.raises(IntegrityError):
        await transact(
            repository,
            lambda uow: uow.builds.save(duplicate),
            commit=True,
        )


async def test_repository_round_trips_immutable_public_sponsor_snapshot(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    installation_id = uuid.UUID("77777777-7777-4777-8777-777777777777")
    sponsor = PublicSponsor(
        installation_id,
        display_name="Example server",
        address="play.example.test",
        website_url="https://example.test/server",
    )
    build = submission_build(BuildCategory.OTHER, account_id, sponsor=sponsor)

    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))

    assert loaded is not None
    assert loaded.sponsor == sponsor
    async with migrated_session_factory() as session:
        row = (
            await session.execute(
                select(
                    Build.sponsor_installation_id,
                    Build.sponsor_display_name,
                    Build.sponsor_address,
                    Build.sponsor_website_url,
                ).where(Build.id == build.id)
            )
        ).one()
    assert row == (installation_id, "Example server", "play.example.test", "https://example.test/server")


async def test_public_summaries_select_only_allowlisted_fields_and_preserve_requested_order(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    confirmed = submission_build(BuildCategory.OTHER, account_id)
    confirmed.submission_status = Status.CONFIRMED
    confirmed.display_name = "Public prototype"
    confirmed.description = "moderation-only description"
    confirmed.extra_info = {"submission_provenance": {"private": True}}
    confirmed.links = [
        BuildLink(url="https://cdn.example.test/render.png", media_type="render"),
        BuildLink(url="https://cdn.example.test/evidence.mp4", media_type="video"),
    ]
    pending = submission_build(BuildCategory.OTHER, account_id)
    await transact(
        repository,
        lambda uow: uow.builds.save(confirmed),
        commit=True,
    )
    await transact(
        repository,
        lambda uow: uow.builds.save(pending),
        commit=True,
    )
    assert confirmed.id is not None
    assert pending.id is not None

    async with migrated_session_factory() as session:
        summaries = await PublicSummaryOnlyBuildRepository(lambda: session).get_public_summaries(
            [confirmed.id, pending.id, confirmed.id, 999_999]
        )

    assert [summary.id for summary in summaries] == [confirmed.id, confirmed.id]
    assert summaries[0] == summaries[1]
    assert summaries[0].display_name == "Public prototype"
    assert summaries[0].preview is not None
    assert summaries[0].preview.url == "https://cdn.example.test/render.png"


async def _claim_submission(
    sessions: async_sessionmaker[AsyncSession], payload: NormalizedSubmission
) -> ClaimedFinalizationJob:
    """Persist a fresh draft and payload, then commit its claim before execution."""
    current = Instant.now()
    draft = StoredDraft(
        DraftSnapshot(
            payload.source_draft_id, payload.owner_account_id, "build_submission.v1", 1, payload.category.value
        ),
        payload.origin,
        current,
        current,
        current.add(days=7, days_assumed_24h_ok=True),
        source_installation_id=payload.source_installation_id,
    )
    await transact(
        partial(SqlAlchemyUnitOfWork, sessions), lambda uow: uow.submission_drafts.create(draft), commit=True
    )
    jobs = partial(SqlAlchemyUnitOfWork, sessions)
    await transact(
        jobs,
        lambda uow: uow.finalization_jobs.enqueue(draft, payload, now=current, expires_at=draft.expires_at),
        commit=True,
    )
    (claim,) = await transact(jobs, lambda uow: uow.finalization_jobs.claim(now=current, limit=1), commit=True)
    return claim


async def _execute_submission(
    preparation: SubmissionBuildPreparation, sessions: async_sessionmaker[AsyncSession], payload: NormalizedSubmission
) -> FinalizedBuild | BuildSubmissionRejected:
    claim = await _claim_submission(sessions, payload)
    result = await BuildSubmissionExecutor(preparation, uow_factory=partial(SqlAlchemyUnitOfWork, sessions)).execute(
        claim, now=Instant.now()
    )
    assert result is not None
    return result


async def test_submission_target_persists_only_the_exact_canonical_source_version(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    versions = VersionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    builds = BuildService(
        BuildLockService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
        RestrictionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
        versions,
        NoopEmbeddings(),
        BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
        uow_factory=repository,
    )
    target = SubmissionBuildPreparation(builds, NoApprovedTags(), versions)
    canonical_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777777")

    result = await _execute_submission(
        target, migrated_session_factory, normalized_submission(account_id, canonical_draft_id, "Java 1.21.0")
    )

    assert isinstance(result, FinalizedBuild)
    persisted = await transact(repository, lambda uow: uow.builds.get_by_id(result.build_id))
    assert persisted is not None
    assert persisted.versions == ["Java 1.21.0"]

    unknown_draft_id = uuid.UUID("88888888-8888-4888-8888-888888888888")
    rejected = await _execute_submission(
        target, migrated_session_factory, normalized_submission(account_id, unknown_draft_id, "Java 1.21.99")
    )

    assert rejected == BuildSubmissionRejected(
        (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
    )
    assert (
        await transact(repository, lambda uow: uow.builds.get_by_source_submission_draft_id(unknown_draft_id)) is None
    )

    invalid_build = submission_build(BuildCategory.OTHER, account_id)
    invalid_build.versions = ["Java 1.21.99"]
    with pytest.raises(InvalidBuildError, match="Unknown canonical Minecraft versions"):
        await transact(
            repository,
            lambda uow: uow.builds.save(invalid_build),
            commit=True,
        )
    assert invalid_build.id is None
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Build)) == 1


async def test_concurrent_source_draft_writes_publish_and_materialize_one_submission_event(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    draft_id = uuid.UUID("99999999-9999-4999-8999-999999999999")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    versions = VersionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    writer = SubmissionBuildPreparation(
        BuildService(
            BuildLockService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
            RestrictionService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
            versions,
            NoopEmbeddings(),
            BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)),
            uow_factory=repository,
        ),
        NoApprovedTags(),
        versions,
    )
    claim = await _claim_submission(
        migrated_session_factory, normalized_submission(account_id, draft_id, "Java 1.21.0")
    )
    executor = BuildSubmissionExecutor(writer, uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    results: list[FinalizedBuild | BuildSubmissionRejected | None] = []

    async def create() -> None:
        results.append(await executor.execute(claim, now=Instant.now()))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(create)
        tasks.start_soon(create)

    assert len(results) == 2
    assert sum(result is None for result in results) == 1
    result = next(result for result in results if result is not None)
    assert isinstance(result, FinalizedBuild)

    async with build_edit_session(migrated_session_factory) as session:
        staff = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add(staff)
        await session.flush()
        role_id = await session.scalar(
            select(PermissionRole.id).where(PermissionRole.builtin_key == BuiltinRoleKeys.GLOBAL_ADMIN)
        )
        assert role_id is not None
        session.add(PermissionRoleAssignment(role_id=role_id, subject_account_id=staff.id))
        session.add(NotificationProfile(account_id=staff.id, web_enabled=True, dm_enabled=False))

    async with migrated_session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Build).where(Build.source_submission_draft_id == draft_id)
            )
            == 1
        )
        events = tuple(
            (
                await session.scalars(
                    select(DomainEvent).where(
                        DomainEvent.event_type == "build.submitted",
                        DomainEvent.aggregate_id == result.build_id,
                    )
                )
            ).all()
        )
    assert len(events) == 1
    event = events[0]
    notification_event = DomainEvent(
        id=event.id,
        event_type=event.event_type,
        aggregate_kind=event.aggregate_kind,
        aggregate_id=event.aggregate_id,
        occurred_at=event.occurred_at,
        payload=event.payload,
        schema_version=event.schema_version,
    )
    notifications = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    await transact(notifications, lambda uow: uow.notifications.materialize(notification_event), commit=True)
    await transact(notifications, lambda uow: uow.notifications.materialize(notification_event), commit=True)

    async with migrated_session_factory() as session:
        materialized = tuple(
            (
                await session.scalars(
                    select(InboxNotification).where(InboxNotification.event_id == notification_event.id)
                )
            ).all()
        )
    assert len(materialized) == 1
    assert materialized[0].account_id == staff.id


async def test_notification_materialize_merge_and_replay_coalesces_recipient_state(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await seed_account_and_version(migrated_session_factory)
    build = submission_build(BuildCategory.OTHER, owner_id)
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None

    async with build_edit_session(migrated_session_factory) as session:
        survivor = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        absorbed = Account(consent_version=CURRENT_CONSENT_VERSION, consented_at=Instant.now())
        session.add_all([survivor, absorbed])
        await session.flush()
        role_id = await session.scalar(
            select(PermissionRole.id).where(PermissionRole.builtin_key == BuiltinRoleKeys.GLOBAL_ADMIN)
        )
        assert role_id is not None
        session.add_all(
            [
                AccountIdentity(
                    account_id=survivor.id,
                    provider=IdentityProvider.DISCORD,
                    subject="700000000000000001",
                ),
                AccountIdentity(
                    account_id=absorbed.id,
                    provider=IdentityProvider.DISCORD,
                    subject="700000000000000002",
                ),
                PermissionRoleAssignment(role_id=role_id, subject_account_id=survivor.id),
                PermissionRoleAssignment(role_id=role_id, subject_account_id=absorbed.id),
                NotificationProfile(account_id=survivor.id, web_enabled=True, dm_enabled=True),
                NotificationProfile(account_id=absorbed.id, web_enabled=True, dm_enabled=True),
            ]
        )
        event = DomainEvent(event_type="build.submitted", aggregate_kind="build", aggregate_id=build.id)
        session.add(event)
        await session.flush()
        survivor_id = survivor.id
        absorbed_id = absorbed.id
        notification_event = DomainEvent(
            id=event.id,
            event_type=event.event_type,
            aggregate_kind=event.aggregate_kind,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            schema_version=event.schema_version,
        )

    notifications = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await transact(notifications, lambda uow: uow.notifications.materialize(notification_event), commit=True)
    claimed = await transact(notifications, lambda uow: uow.notifications.claim_deliveries(limit=10), commit=True)
    assert len(claimed) == 2
    stale_absorbed_claim = next(delivery for delivery in claimed if delivery.discord_id == 700000000000000002)
    seeded_at = Instant.now()
    async with build_edit_session(migrated_session_factory) as session:
        rows = tuple(
            (
                await session.scalars(
                    select(InboxNotification)
                    .where(InboxNotification.event_id == notification_event.id)
                    .order_by(InboxNotification.account_id)
                )
            ).all()
        )
        assert len(rows) == 2
        by_account = {row.account_id: row for row in rows}
        by_account[absorbed_id].read_at = seeded_at
        deliveries = tuple(
            (
                await session.scalars(
                    select(NotificationDeliveryRecord).where(
                        NotificationDeliveryRecord.notification_id.in_([row.id for row in rows])
                    )
                )
            ).all()
        )
        assert len(deliveries) == 2
        for delivery in deliveries:
            if delivery.account_id == survivor_id:
                delivery.sent_at = seeded_at

    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper"),
        lambda uow: uow.accounts.merge(survivor_id, absorbed_id),
        commit=True,
    )
    assert (
        await transact(
            notifications, lambda uow: uow.notifications.complete_delivery(stale_absorbed_claim), commit=True
        )
        is False
    )
    await transact(notifications, lambda uow: uow.notifications.materialize(notification_event), commit=True)

    async with migrated_session_factory() as session:
        merged = tuple(
            (
                await session.scalars(
                    select(InboxNotification).where(InboxNotification.event_id == notification_event.id)
                )
            ).all()
        )
        merged_deliveries = tuple((await session.scalars(select(NotificationDeliveryRecord))).all())
    assert len(merged) == 1
    assert merged[0].account_id == survivor_id
    assert merged[0].source_key == f"event:{notification_event.id}:staff:{survivor_id}"
    assert merged[0].read_at == seeded_at.round("microsecond", mode="floor")
    assert len(merged_deliveries) == 1
    assert merged_deliveries[0].account_id == survivor_id
    assert merged_deliveries[0].sent_at == seeded_at.round("microsecond", mode="floor")
    assert merged_deliveries[0].claimed_at is None
    assert merged_deliveries[0].claim_token is None
    assert merged_deliveries[0].attempts == 0


async def test_account_merge_transfers_submission_and_minecraft_authorization_ownership(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    java_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
    survivor = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    absorbed = await transact(
        accounts, lambda uow: uow.accounts.create(identities=(AccountIdentityValue.java(java_uuid),)), commit=True
    )
    assert survivor.id is not None
    assert absorbed.id is not None
    draft_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    installation_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    challenge_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
    grant_id = uuid.UUID("66666666-6666-6666-6666-666666666666")
    installation_secret_hash = bytes.fromhex("11" * 32)
    seeded_at = Instant.now()
    build = submission_build(BuildCategory.UTILITY, absorbed.id)
    build.versions = []
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    build_id = build.id

    async with build_edit_session(migrated_session_factory) as session:
        session.add_all(
            [
                SubmissionDraft(
                    id=draft_id,
                    owner_account_id=absorbed.id,
                    schema_id="redstone_squid.submission",
                    schema_revision=1,
                    category="utility",
                    origin=SubmissionOrigin.PAPER,
                    source_installation_id=installation_id,
                    expires_at=seeded_at.add(days=7, days_assumed_24h_ok=True),
                ),
                PaperInstallation(
                    id=installation_id,
                    owner_account_id=absorbed.id,
                    label="Merge test",
                    secret_hash=installation_secret_hash,
                ),
                SchematicFile(
                    sha256="merge-test-sha",
                    byte_size=1,
                    source_format="schem",
                    object_key="schematics/me/merge-test-sha",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                SubmissionDraftAccess(draft_id=draft_id, account_id=absorbed.id, role="owner"),
                SubmissionDraftAccess(draft_id=draft_id, account_id=survivor.id, role="editor"),
                SubmissionDraftChange(
                    draft_id=draft_id,
                    actor_account_id=absorbed.id,
                    base_revision=0,
                    resulting_revision=1,
                    client_instance_id="web-test",
                    idempotency_key="merge-test-key",
                    operations=[{"op": "set"}],
                ),
                BuildSchematic(
                    build_id=build_id,
                    file_sha256="merge-test-sha",
                    is_primary=True,
                    width=1,
                    height=1,
                    length=1,
                    allocated_width=1,
                    allocated_height=1,
                    allocated_length=1,
                    block_count=1,
                    bounding_volume=1,
                    palette_size=1,
                    analyzer_version="test-1",
                    analysis_schema_version=1,
                    visibility="reviewer_only",
                    rights_attested_at=seeded_at,
                    rights_attested_by_account_id=absorbed.id,
                ),
                PlayerAuthorizationChallenge(
                    id=challenge_id,
                    device_code_hash=bytes.fromhex("22" * 32),
                    user_code_hash=bytes.fromhex("33" * 32),
                    origin=MinecraftClientOrigin.PAPER,
                    java_uuid=java_uuid,
                    installation_id=installation_id,
                    installation_credential_version=1,
                    created_at=seeded_at,
                    expires_at=seeded_at.add(minutes=10),
                    approved_by_account_id=absorbed.id,
                    approved_at=seeded_at,
                    exchanged_at=seeded_at,
                ),
            ]
        )
        await session.flush()
        session.add(
            PlayerGrant(
                id=grant_id,
                challenge_id=challenge_id,
                token_hash=bytes.fromhex("44" * 32),
                account_id=absorbed.id,
                java_uuid=java_uuid,
                origin=MinecraftClientOrigin.PAPER,
                installation_id=installation_id,
                installation_credential_version=1,
                issued_at=seeded_at,
                expires_at=seeded_at.add(minutes=5),
            )
        )

    await transact(accounts, lambda uow: uow.accounts.merge(survivor.id, absorbed.id), commit=True)

    async with migrated_session_factory() as session:
        build_owner = await session.scalar(select(Build.submitter_account_id).where(Build.id == build_id))
        draft_owner = (
            await session.execute(
                select(SubmissionDraft.owner_account_id, SubmissionDraft.source_installation_id).where(
                    SubmissionDraft.id == draft_id
                )
            )
        ).one()
        access = (
            await session.execute(
                select(SubmissionDraftAccess.account_id, SubmissionDraftAccess.role).where(
                    SubmissionDraftAccess.draft_id == draft_id
                )
            )
        ).all()
        change_actor = await session.scalar(
            select(SubmissionDraftChange.actor_account_id).where(SubmissionDraftChange.draft_id == draft_id)
        )
        rights_actor = await session.scalar(
            select(BuildSchematic.rights_attested_by_account_id).where(BuildSchematic.build_id == build_id)
        )
        installation = (
            await session.execute(
                select(
                    PaperInstallation.owner_account_id,
                    PaperInstallation.id,
                    PaperInstallation.secret_hash,
                ).where(PaperInstallation.id == installation_id)
            )
        ).one()
        challenge = (
            await session.execute(
                select(
                    PlayerAuthorizationChallenge.approved_by_account_id,
                    PlayerAuthorizationChallenge.java_uuid,
                    PlayerAuthorizationChallenge.installation_id,
                ).where(PlayerAuthorizationChallenge.id == challenge_id)
            )
        ).one()
        grant = (
            await session.execute(
                select(
                    PlayerGrant.account_id,
                    PlayerGrant.java_uuid,
                    PlayerGrant.installation_id,
                ).where(PlayerGrant.id == grant_id)
            )
        ).one()
        java_identity_owner = await session.scalar(
            select(AccountIdentity.account_id).where(
                AccountIdentity.provider == IdentityProvider.JAVA,
                AccountIdentity.subject == str(java_uuid),
            )
        )

    assert build_owner == survivor.id
    assert draft_owner == (survivor.id, installation_id)
    assert access == [(survivor.id, "owner")]
    assert change_actor == survivor.id
    assert rights_actor == survivor.id
    assert installation == (survivor.id, installation_id, installation_secret_hash)
    assert challenge == (survivor.id, java_uuid, installation_id)
    assert grant == (survivor.id, java_uuid, installation_id)
    assert java_identity_owner == survivor.id
    assert await transact(accounts, lambda uow: uow.accounts.get_by_id(absorbed.id)) is None


async def test_account_merge_rewrites_pending_payloads_and_fences_claimed_work(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    survivor = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    absorbed = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    assert survivor.id is not None
    assert absorbed.id is not None
    pending_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777771")
    claimed_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777772")
    installation_id = uuid.UUID("77777777-7777-4777-8777-777777777773")
    sponsor = PublicSponsor(installation_id, display_name="Merge-safe server")
    queued_at = Instant.now()

    absorbed_id = absorbed.id

    def draft(draft_id: uuid.UUID) -> StoredDraft:
        return StoredDraft(
            snapshot=DraftSnapshot(
                id=draft_id,
                owner_account_id=absorbed_id,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
            ),
            origin=SubmissionOrigin.PAPER,
            source_installation_id=installation_id,
            created_at=queued_at,
            updated_at=queued_at,
            expires_at=queued_at.add(days=7, days_assumed_24h_ok=True),
        )

    pending_draft = draft(pending_draft_id)
    claimed_draft = draft(claimed_draft_id)
    drafts = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await transact(drafts, lambda uow: uow.submission_drafts.create(pending_draft), commit=True)
    await transact(drafts, lambda uow: uow.submission_drafts.create(claimed_draft), commit=True)
    pending_payload = replace(
        normalized_submission(absorbed.id, pending_draft_id, "Java 1.21.0"),
        origin=SubmissionOrigin.PAPER,
        source_installation_id=installation_id,
    )
    claimed_payload = replace(
        normalized_submission(absorbed.id, claimed_draft_id, "Java 1.21.0"),
        origin=SubmissionOrigin.PAPER,
        sponsor_attribution=True,
        source_installation_id=installation_id,
        sponsor=sponsor,
    )
    finalizations = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    # Delay one job so the pre-merge claim selects only the other draft.
    await transact(
        finalizations,
        lambda uow: uow.finalization_jobs.enqueue(
            pending_draft,
            pending_payload,
            now=queued_at.add(days=1, days_assumed_24h_ok=True),
            expires_at=pending_draft.expires_at,
        ),
        commit=True,
    )
    await transact(
        finalizations,
        lambda uow: uow.finalization_jobs.enqueue(
            claimed_draft, claimed_payload, now=queued_at, expires_at=claimed_draft.expires_at
        ),
        commit=True,
    )
    (stale_claim,) = await transact(
        finalizations, lambda uow: uow.finalization_jobs.claim(now=queued_at, limit=1), commit=True
    )
    assert stale_claim.draft_id == claimed_draft_id

    build = submission_build(BuildCategory.OTHER, absorbed.id, draft_id=claimed_draft_id, sponsor=sponsor)
    build.versions = []
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    build_id = build.id

    await transact(accounts, lambda uow: uow.accounts.merge(survivor.id, absorbed.id), commit=True)

    result = FinalizedBuild(build_id)
    async with build_edit_session(migrated_session_factory) as session:
        assert (
            await PostgresFinalizationJobRepository(lambda: session).complete(stale_claim, result, now=Instant.now())
            is False
        )
    (replacement_claim,) = await transact(
        finalizations, lambda uow: uow.finalization_jobs.claim(now=Instant.now().add(minutes=1), limit=1), commit=True
    )
    assert replacement_claim.draft_id == claimed_draft_id
    assert replacement_claim.payload.owner_account_id == survivor.id
    assert replacement_claim.payload.sponsor == sponsor
    async with build_edit_session(migrated_session_factory) as session:
        assert (
            await PostgresFinalizationJobRepository(lambda: session).complete(
                replacement_claim, result, now=Instant.now()
            )
            is True
        )

    async with migrated_session_factory() as session:
        jobs = {
            job.draft_id: job
            for job in (
                await session.scalars(
                    select(SubmissionFinalizationJob).where(
                        SubmissionFinalizationJob.draft_id.in_((pending_draft_id, claimed_draft_id))
                    )
                )
            ).all()
        }
        build_owner = await session.scalar(select(Build.submitter_account_id).where(Build.id == build_id))
        pending_status = await session.scalar(
            select(SubmissionDraft.status).where(SubmissionDraft.id == pending_draft_id)
        )
        original_inputs = {
            original.job_id: original
            for original in (
                await session.scalars(
                    select(SubmissionFinalizationInput).where(
                        SubmissionFinalizationInput.job_id.in_(tuple(job.id for job in jobs.values()))
                    )
                )
            ).all()
        }

    # Execution payloads follow the merged owner; immutable inputs retain the original request.
    pending_job = jobs[pending_draft_id]
    completed_job = jobs[claimed_draft_id]
    assert pending_job.status == FinalizationJobStatus.PENDING.value
    assert pending_job.payload is not None
    assert pending_job.payload["payload_schema"] == 2
    assert pending_job.payload["owner_account_id"] == survivor.id
    assert pending_job.payload_sha256 == submission_payload_digest(pending_job.payload)
    assert original_inputs[pending_job.id].payload["owner_account_id"] == absorbed.id
    assert original_inputs[pending_job.id].payload_sha256 != pending_job.payload_sha256
    assert completed_job.status == FinalizationJobStatus.COMPLETED.value
    assert completed_job.payload is not None
    assert completed_job.payload["payload_schema"] == 3
    assert completed_job.payload["owner_account_id"] == survivor.id
    assert completed_job.payload_sha256 == submission_payload_digest(completed_job.payload)
    assert original_inputs[completed_job.id].payload["owner_account_id"] == absorbed.id
    assert original_inputs[completed_job.id].payload_sha256 != completed_job.payload_sha256
    assert stale_claim.claim_token != replacement_claim.claim_token
    assert replacement_claim.attempts == stale_claim.attempts + 1
    assert build_owner == survivor.id
    assert pending_status is DraftStatus.PROCESSING

    with pytest.raises(DBAPIError, match="submission finalization inputs are immutable"):
        async with build_edit_session(migrated_session_factory) as session:
            await session.execute(
                update(SubmissionFinalizationInput)
                .where(SubmissionFinalizationInput.job_id == pending_job.id)
                .values(payload_sha256="0" * 64)
            )


async def test_account_merge_refuses_to_bless_a_conflicting_finalization_digest(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    survivor = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    absorbed = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    assert survivor.id is not None
    assert absorbed.id is not None
    draft_id = uuid.UUID("77777777-7777-4777-8777-777777777774")
    queued_at = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(
            id=draft_id,
            owner_account_id=absorbed.id,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="other",
        ),
        origin=SubmissionOrigin.WEB,
        created_at=queued_at,
        updated_at=queued_at,
        expires_at=queued_at.add(days=7, days_assumed_24h_ok=True),
    )
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.submission_drafts.create(draft),
        commit=True,
    )
    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.finalization_jobs.enqueue(
            draft,
            normalized_submission(absorbed.id, draft_id, "Java 1.21.0"),
            now=queued_at,
            expires_at=draft.expires_at,
        ),
        commit=True,
    )
    async with build_edit_session(migrated_session_factory) as session:
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(SubmissionFinalizationJob.draft_id == draft_id)
            .values(payload_sha256="0" * 64)
        )

    with pytest.raises(DataIntegrityError, match="integrity check"):
        await transact(accounts, lambda uow: uow.accounts.merge(survivor.id, absorbed.id), commit=True)

    assert await transact(accounts, lambda uow: uow.accounts.get_by_id(absorbed.id)) is not None
    async with migrated_session_factory() as session:
        draft_owner = await session.scalar(
            select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == draft_id)
        )
        payload_sha256 = await session.scalar(
            select(SubmissionFinalizationJob.payload_sha256).where(SubmissionFinalizationJob.draft_id == draft_id)
        )
    assert draft_owner == absorbed.id
    assert payload_sha256 == "0" * 64


async def test_account_merge_carries_permission_rules_and_keeps_the_stricter_effect(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    survivor = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    absorbed = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    assert survivor.id is not None
    assert absorbed.id is not None
    async with build_edit_session(migrated_session_factory) as session:
        builtin_roles = {
            role.builtin_key: role
            for role in (
                await session.scalars(
                    select(PermissionRole).where(
                        PermissionRole.builtin_key.in_(
                            (BuiltinRoleKeys.GLOBAL_ADMIN.value, BuiltinRoleKeys.TRUSTED.value)
                        )
                    )
                )
            ).all()
        }
        global_admin = builtin_roles[BuiltinRoleKeys.GLOBAL_ADMIN.value]
        trusted = builtin_roles[BuiltinRoleKeys.TRUSTED.value]
        custom_role = PermissionRole(
            slug="legacy-crew",
            name="Legacy crew",
            guild_id=123,
            created_by_account_id=absorbed.id,
        )
        session.add(custom_role)
        await session.flush()
        session.add_all(
            [
                PermissionGrant(
                    pattern="build.submission.edit",
                    effect=1,
                    subject_account_id=survivor.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionGrant(
                    pattern="build.submission.edit",
                    effect=-2,
                    subject_account_id=absorbed.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionGrant(
                    pattern="vote.poll.cast",
                    effect=1,
                    subject_account_id=absorbed.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionRoleAssignment(
                    role_id=global_admin.id,
                    subject_account_id=survivor.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionRoleAssignment(
                    role_id=global_admin.id,
                    subject_account_id=absorbed.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionRoleAssignment(
                    role_id=trusted.id,
                    subject_account_id=absorbed.id,
                    granted_by_account_id=absorbed.id,
                ),
                PermissionRolePattern(
                    role_id=custom_role.id,
                    pattern="build.submission.edit",
                    mode=1,
                    added_by_account_id=absorbed.id,
                ),
                PermissionAuditEntry(
                    action="grant",
                    actor_account_id=absorbed.id,
                    subject_kind="account",
                    subject_id=absorbed.id,
                ),
            ]
        )

    await transact(accounts, lambda uow: uow.accounts.merge(survivor.id, absorbed.id), commit=True)

    async with migrated_session_factory() as session:
        grants = (
            await session.execute(
                select(
                    PermissionGrant.pattern,
                    PermissionGrant.effect,
                    PermissionGrant.subject_account_id,
                    PermissionGrant.granted_by_account_id,
                ).order_by(PermissionGrant.pattern)
            )
        ).all()
        assignments = (
            await session.execute(
                select(
                    PermissionRole.builtin_key,
                    PermissionRoleAssignment.subject_account_id,
                    PermissionRoleAssignment.granted_by_account_id,
                )
                .join(PermissionRole, PermissionRole.id == PermissionRoleAssignment.role_id)
                .order_by(PermissionRole.builtin_key)
            )
        ).all()
        role_creator = await session.scalar(
            select(PermissionRole.created_by_account_id).where(PermissionRole.slug == "legacy-crew")
        )
        pattern_author = await session.scalar(
            select(PermissionRolePattern.added_by_account_id).where(PermissionRolePattern.role_id == custom_role.id)
        )
        audit = (
            await session.execute(
                select(PermissionAuditEntry.actor_account_id, PermissionAuditEntry.subject_id).where(
                    PermissionAuditEntry.action == "grant"
                )
            )
        ).one()

    # The absorbed forbid outranks the survivor's own allow on the same pattern and scope.
    assert grants == [
        ("build.submission.edit", -2, survivor.id, survivor.id),
        ("vote.poll.cast", 1, survivor.id, survivor.id),
    ]
    assert assignments == [
        ("global-admin", survivor.id, survivor.id),
        ("trusted", survivor.id, survivor.id),
    ]
    assert role_creator == survivor.id
    assert pattern_author == survivor.id
    assert audit == (survivor.id, survivor.id)
    assert await transact(accounts, lambda uow: uow.accounts.get_by_id(absorbed.id)) is None


async def test_imported_door_with_unknown_facts_commits_and_reloads(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = await seed_account_and_version(migrated_session_factory)
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    versions = VersionService(uow_factory=factory)
    builds = BuildService(
        BuildLockService(uow_factory=factory),
        RestrictionService(uow_factory=factory),
        versions,
        NoopEmbeddings(),
        BuildTaxonomyService(uow_factory=factory),
        uow_factory=factory,
    )
    payload = replace(
        normalized_submission(owner, uuid.UUID(int=555), "Java 1.21.0"),
        category=SubmissionCategory.DOOR,
        origin=SubmissionOrigin.DISCORD,
        creators=(),
        capture_dimensions=SubmissionDimensions(None, None, None),
        source_version=None,
        details=DoorSubmissionDetails(SubmissionDimensions(None, 3, None), None),
    )

    result = await _execute_submission(
        SubmissionBuildPreparation(builds, NoApprovedTags(), versions), migrated_session_factory, payload
    )

    assert isinstance(result, FinalizedBuild)
    loaded = await transact(factory, lambda uow: uow.builds.get_by_id(result.build_id))
    assert isinstance(loaded, DoorBuild)
    assert loaded.door_dimensions == (None, 3, None)
    assert loaded.orientation is None
    assert loaded.creators_ign == []
    assert loaded.versions == []
