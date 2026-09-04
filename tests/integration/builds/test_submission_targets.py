"""PostgreSQL coverage for account-keyed synchronized build targets."""

import uuid
from dataclasses import replace

import anyio
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.accounts.domain import AccountIdentity as AccountIdentityValue
from squid.accounts.infrastructure.models import Account, AccountIdentity
from squid.accounts.infrastructure.repository import AccountRepository
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.locks import BuildLockRepository
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.infrastructure.restrictions import RestrictionRepository
from squid.builds.infrastructure.taxonomy import OfficialTagResolver
from squid.core.errors import DataIntegrityError
from squid.events import DomainEvent
from squid.events.infrastructure.models import DomainEventRecord
from squid.minecraft_auth.infrastructure.models import PaperInstallationRecord, PlayerChallengeRecord, PlayerGrantRecord
from squid.notifications.infrastructure.models import NotificationProfile, NotificationRecord
from squid.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.permissions.domain import BuiltinRoleKeys
from squid.permissions.infrastructure.models import (
    PermissionAuditEntry,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRolePattern,
)
from squid.schematics.infrastructure.models import BuildSchematic, SchematicFile
from squid.sponsors import PublicSponsor
from squid.submissions.application import StoredDraft
from squid.submissions.domain import (
    BuildSubmissionRejected,
    DraftSnapshot,
    DraftStatus,
    FinalizationJobStatus,
    FinalizedBuild,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionOrigin,
)
from squid.submissions.infrastructure.build_target import CanonicalBuildSubmissionWriter
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.infrastructure.models import SubmissionDraft, SubmissionDraftAccess, SubmissionDraftChange
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from squid.submissions.payload_integrity import submission_payload_digest
from squid.tags.domain import TagDefinition
from squid.versions.application import VersionService
from squid.versions.infrastructure.repository import VersionRepository
from tests.support.submission_targets import normalized_submission, seed_account_and_version, submission_build


class NoopEmbeddings:
    async def prepare(self, build: Build) -> None:
        del build

    async def index(self, build: Build) -> None:
        del build


class NoApprovedTags:
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return ()


async def test_repository_round_trips_and_updates_every_manifest_category(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    draft_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    builds = [
        submission_build(category, account_id, draft_id=draft_id if category is BuildCategory.OTHER else None)
        for category in BuildCategory
    ]

    for build in builds:
        await repository.save(build)
        assert build.id is not None

    loaded = [await repository.get_by_id(build.id) for build in builds if build.id is not None]
    assert [build.category if build is not None else None for build in loaded] == list(BuildCategory)
    assert all(build is not None and build.submitter_account_id == account_id for build in loaded)
    assert all(build is not None and build.submitter_discord_id is None for build in loaded)
    other = await repository.get_by_source_submission_draft_id(draft_id)
    assert other is not None
    assert other.category is BuildCategory.OTHER
    assert other.display_name == "Workshop prototype"
    pending = await repository.get_pending()
    assert {build.category for build in pending} == set(BuildCategory)
    account_page = await repository.list_page(
        statuses=frozenset({Status.PENDING}),
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

    duplicate = submission_build(BuildCategory.OTHER, account_id, draft_id=draft_id)
    with pytest.raises(IntegrityError):
        await repository.save(duplicate)


async def test_repository_round_trips_immutable_public_sponsor_snapshot(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    installation_id = uuid.UUID("77777777-7777-4777-8777-777777777777")
    sponsor = PublicSponsor(
        installation_id,
        display_name="Example server",
        address="play.example.test",
        website_url="https://example.test/server",
    )
    build = submission_build(BuildCategory.OTHER, account_id, sponsor=sponsor)

    await repository.save(build)
    assert build.id is not None
    loaded = await repository.get_by_id(build.id)

    assert loaded is not None
    assert loaded.sponsor == sponsor
    async with migrated_session_factory() as session:
        row = (
            await session.execute(
                select(
                    SQLBuild.sponsor_installation_id,
                    SQLBuild.sponsor_display_name,
                    SQLBuild.sponsor_address,
                    SQLBuild.sponsor_website_url,
                ).where(SQLBuild.id == build.id)
            )
        ).one()
    assert row == (installation_id, "Example server", "play.example.test", "https://example.test/server")


async def test_submission_target_persists_only_the_exact_canonical_source_version(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    versions = VersionService(VersionRepository(migrated_session_factory))
    builds = BuildService(
        repository,
        BuildLockRepository(migrated_session_factory),
        RestrictionRepository(migrated_session_factory),
        versions,
        NoopEmbeddings(),
        OfficialTagResolver(migrated_session_factory),
    )
    target = CanonicalBuildSubmissionWriter(builds, NoApprovedTags(), versions)
    canonical_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777777")

    result = await target.create_or_get(normalized_submission(account_id, canonical_draft_id, "Java 1.21.0"))

    assert isinstance(result, FinalizedBuild)
    persisted = await repository.get_by_id(result.build_id)
    assert persisted is not None
    assert persisted.versions == ["Java 1.21.0"]

    unknown_draft_id = uuid.UUID("88888888-8888-4888-8888-888888888888")
    rejected = await target.create_or_get(normalized_submission(account_id, unknown_draft_id, "Java 1.21.99"))

    assert rejected == BuildSubmissionRejected(
        (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
    )
    assert await repository.get_by_source_submission_draft_id(unknown_draft_id) is None

    invalid_build = submission_build(BuildCategory.OTHER, account_id)
    invalid_build.versions = ["Java 1.21.99"]
    with pytest.raises(InvalidBuildError, match="Unknown canonical Minecraft versions"):
        await repository.save(invalid_build)
    assert invalid_build.id is None
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 1


async def test_concurrent_source_draft_writes_publish_and_materialize_one_submission_event(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await seed_account_and_version(migrated_session_factory)
    draft_id = uuid.UUID("99999999-9999-4999-8999-999999999999")
    repository = BuildRepository(migrated_session_factory)
    versions = VersionService(VersionRepository(migrated_session_factory))
    writer = CanonicalBuildSubmissionWriter(
        BuildService(
            repository,
            BuildLockRepository(migrated_session_factory),
            RestrictionRepository(migrated_session_factory),
            versions,
            NoopEmbeddings(),
            OfficialTagResolver(migrated_session_factory),
        ),
        NoApprovedTags(),
        versions,
    )
    results: list[FinalizedBuild | BuildSubmissionRejected] = []

    async def create() -> None:
        results.append(await writer.create_or_get(normalized_submission(account_id, draft_id, "Java 1.21.0")))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(create)
        tasks.start_soon(create)

    assert len(results) == 2
    assert all(isinstance(result, FinalizedBuild) for result in results)
    assert results[0] == results[1]
    result = results[0]
    assert isinstance(result, FinalizedBuild)

    async with migrated_session_factory.begin() as session:
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
                select(func.count()).select_from(SQLBuild).where(SQLBuild.source_submission_draft_id == draft_id)
            )
            == 1
        )
        events = tuple(
            (
                await session.scalars(
                    select(DomainEventRecord).where(
                        DomainEventRecord.event_type == "build.submitted",
                        DomainEventRecord.aggregate_id == result.build_id,
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
    notifications = PostgresNotificationRepository(migrated_session_factory)

    await notifications.materialize(notification_event)
    await notifications.materialize(notification_event)

    async with migrated_session_factory() as session:
        materialized = tuple(
            (
                await session.scalars(
                    select(NotificationRecord).where(NotificationRecord.event_id == notification_event.id)
                )
            ).all()
        )
    assert len(materialized) == 1
    assert materialized[0].account_id == staff.id


async def test_account_merge_transfers_submission_and_minecraft_authorization_ownership(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    java_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
    survivor = await accounts.create()
    absorbed = await accounts.create(identities=(AccountIdentityValue.java(java_uuid),))
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
    await BuildRepository(migrated_session_factory).save(build)
    assert build.id is not None
    build_id = build.id

    async with migrated_session_factory.begin() as session:
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
                PaperInstallationRecord(
                    id=installation_id,
                    owner_account_id=absorbed.id,
                    label="Merge test",
                    secret_hash=installation_secret_hash,
                ),
                SchematicFile(
                    sha256="merge-test-sha",
                    byte_size=1,
                    source_format="schem",
                    object_key="merge-test-object-key",
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
                PlayerChallengeRecord(
                    id=challenge_id,
                    device_code_hash=bytes.fromhex("22" * 32),
                    user_code_hash=bytes.fromhex("33" * 32),
                    origin="paper",
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
            PlayerGrantRecord(
                id=grant_id,
                challenge_id=challenge_id,
                token_hash=bytes.fromhex("44" * 32),
                account_id=absorbed.id,
                java_uuid=java_uuid,
                origin="paper",
                installation_id=installation_id,
                installation_credential_version=1,
                issued_at=seeded_at,
                expires_at=seeded_at.add(minutes=5),
            )
        )

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        build_owner = await session.scalar(select(SQLBuild.submitter_account_id).where(SQLBuild.id == build_id))
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
                    PaperInstallationRecord.owner_account_id,
                    PaperInstallationRecord.id,
                    PaperInstallationRecord.secret_hash,
                ).where(PaperInstallationRecord.id == installation_id)
            )
        ).one()
        challenge = (
            await session.execute(
                select(
                    PlayerChallengeRecord.approved_by_account_id,
                    PlayerChallengeRecord.java_uuid,
                    PlayerChallengeRecord.installation_id,
                ).where(PlayerChallengeRecord.id == challenge_id)
            )
        ).one()
        grant = (
            await session.execute(
                select(
                    PlayerGrantRecord.account_id,
                    PlayerGrantRecord.java_uuid,
                    PlayerGrantRecord.installation_id,
                ).where(PlayerGrantRecord.id == grant_id)
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
    assert await accounts.get_by_id(absorbed.id) is None


async def test_account_merge_rewrites_pending_payloads_and_fences_claimed_work(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    pending_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777771")
    claimed_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777772")
    installation_id = uuid.UUID("77777777-7777-4777-8777-777777777773")
    sponsor = PublicSponsor(installation_id, display_name="Merge-safe server")
    queued_at = Instant.now()

    def draft(draft_id: uuid.UUID) -> StoredDraft:
        return StoredDraft(
            snapshot=DraftSnapshot(
                id=draft_id,
                owner_account_id=absorbed.id,
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
    drafts = PostgresDraftRepository(migrated_session_factory)
    await drafts.create(pending_draft)
    await drafts.create(claimed_draft)
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
    finalizations = PostgresFinalizationJobRepository(migrated_session_factory)
    await finalizations.enqueue(
        pending_draft,
        pending_payload,
        now=queued_at.add(days=1, days_assumed_24h_ok=True),
        expires_at=pending_draft.expires_at,
    )
    await finalizations.enqueue(
        claimed_draft,
        claimed_payload,
        now=queued_at,
        expires_at=claimed_draft.expires_at,
    )
    (stale_claim,) = await finalizations.claim(now=queued_at, limit=1)
    assert stale_claim.draft_id == claimed_draft_id

    build = submission_build(BuildCategory.OTHER, absorbed.id, draft_id=claimed_draft_id, sponsor=sponsor)
    build.versions = []
    await BuildRepository(migrated_session_factory).save(build)
    assert build.id is not None
    build_id = build.id

    await accounts.merge(survivor.id, absorbed.id)

    result = FinalizedBuild(build_id)
    assert await finalizations.complete(stale_claim, result, now=Instant.now()) is False
    (replacement_claim,) = await finalizations.claim(now=Instant.now().add(minutes=1), limit=1)
    assert replacement_claim.draft_id == claimed_draft_id
    assert replacement_claim.payload.owner_account_id == survivor.id
    assert replacement_claim.payload.sponsor == sponsor
    assert await finalizations.complete(replacement_claim, result, now=Instant.now()) is True

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
        build_owner = await session.scalar(select(SQLBuild.submitter_account_id).where(SQLBuild.id == build_id))
        pending_status = await session.scalar(
            select(SubmissionDraft.status).where(SubmissionDraft.id == pending_draft_id)
        )

    pending_job = jobs[pending_draft_id]
    completed_job = jobs[claimed_draft_id]
    assert pending_job.status == FinalizationJobStatus.PENDING.value
    assert pending_job.payload is not None
    assert pending_job.payload["payload_schema"] == 1
    assert pending_job.payload["owner_account_id"] == survivor.id
    assert pending_job.payload_sha256 == submission_payload_digest(pending_job.payload)
    assert completed_job.status == FinalizationJobStatus.COMPLETED.value
    assert completed_job.payload is not None
    assert completed_job.payload["payload_schema"] == 2
    assert completed_job.payload["owner_account_id"] == survivor.id
    assert completed_job.payload_sha256 == submission_payload_digest(completed_job.payload)
    assert stale_claim.claim_token != replacement_claim.claim_token
    assert replacement_claim.attempts == stale_claim.attempts + 1
    assert build_owner == survivor.id
    assert pending_status is DraftStatus.PROCESSING


async def test_account_merge_refuses_to_bless_a_conflicting_finalization_digest(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
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
    await PostgresDraftRepository(migrated_session_factory).create(draft)
    await PostgresFinalizationJobRepository(migrated_session_factory).enqueue(
        draft,
        normalized_submission(absorbed.id, draft_id, "Java 1.21.0"),
        now=queued_at,
        expires_at=draft.expires_at,
    )
    async with migrated_session_factory.begin() as session:
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(SubmissionFinalizationJob.draft_id == draft_id)
            .values(payload_sha256="0" * 64)
        )

    with pytest.raises(DataIntegrityError, match="integrity check"):
        await accounts.merge(survivor.id, absorbed.id)

    assert await accounts.get_by_id(absorbed.id) is not None
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
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    survivor = await accounts.create()
    absorbed = await accounts.create()
    assert survivor.id is not None
    assert absorbed.id is not None
    async with migrated_session_factory.begin() as session:
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

    await accounts.merge(survivor.id, absorbed.id)

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
    assert await accounts.get_by_id(absorbed.id) is None
