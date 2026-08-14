"""PostgreSQL coverage for provider-neutral synchronized build targets."""

import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import AccountIdentity as AccountIdentityValue
from squid.accounts.infrastructure.models import Account
from squid.accounts.infrastructure.repository import AccountRepository
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.locks import BuildLockRepository
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.infrastructure.restrictions import RestrictionRepository
from squid.core.errors import DataIntegrityError
from squid.sponsors import PublicSponsor
from squid.submissions.application import ActionableSubmissionError, StoredDraft
from squid.submissions.domain import (
    DraftSnapshot,
    DraftStatus,
    FinalizationJobStatus,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTargetResult,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.submissions.infrastructure.build_target import BuildSubmissionTarget
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from squid.submissions.payload_integrity import submission_payload_digest
from squid.tags.domain import TagDefinition
from squid.versions.application import VersionService
from squid.versions.infrastructure.models import Version
from squid.versions.infrastructure.repository import VersionRepository


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


class NoopEmbeddings:
    async def prepare(self, build: Build) -> None:
        del build

    async def index(self, build: Build) -> None:
        del build


class NoApprovedTags:
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return ()


def _normalized_submission(account_id: int, draft_id: uuid.UUID, source_version: str) -> NormalizedSubmission:
    return NormalizedSubmission(
        source_draft_id=draft_id,
        owner_account_id=account_id,
        origin=SubmissionOrigin.WEB,
        schema_id="build_submission.v1",
        schema_revision=1,
        category=SubmissionCategory.OTHER,
        display_name="Version integrity test",
        description=None,
        creators=("Builder",),
        capture_dimensions=SubmissionDimensions(3, 4, 5),
        source_version=source_version,
        version_compatibility=None,
        taxonomy=SubmissionTaxonomy(),
        schematic_policy=SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility.REVIEWER_ONLY,
            license=None,
            rights_attested=False,
            include_inventories=False,
            include_free_text=False,
        ),
        completion=None,
        ai_generated=False,
        sponsor_attribution=False,
        artifacts=VerifiedSubmissionArtifacts(),
        details=GeneralSubmissionDetails(),
    )


def _build(
    category: BuildCategory,
    account_id: int,
    *,
    draft_id: uuid.UUID | None = None,
    sponsor: PublicSponsor | None = None,
) -> Build:
    build = Build(
        category=category,
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        source_submission_draft_id=draft_id,
        sponsor=sponsor,
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


async def test_repository_round_trips_immutable_public_sponsor_snapshot(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    installation_id = uuid.UUID("77777777-7777-4777-8777-777777777777")
    sponsor = PublicSponsor(
        installation_id,
        display_name="Example server",
        address="play.example.test",
        website_url="https://example.test/server",
    )
    build = _build(BuildCategory.OTHER, account_id, sponsor=sponsor)

    await repository.save(build)
    assert build.id is not None
    loaded = await repository.get_by_id(build.id)

    assert loaded is not None
    assert loaded.sponsor == sponsor
    async with migrated_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT sponsor_installation_id, sponsor_display_name, sponsor_address, sponsor_website_url "
                    "FROM builds WHERE id = :build_id"
                ),
                {"build_id": build.id},
            )
        ).one()
    assert row == (installation_id, "Example server", "play.example.test", "https://example.test/server")


async def test_submission_target_persists_only_the_exact_canonical_source_version(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_account_and_version(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)
    versions = VersionService(VersionRepository(migrated_session_factory))
    builds = BuildService(
        repository,
        BuildLockRepository(migrated_session_factory),
        RestrictionRepository(migrated_session_factory),
        versions,
        NoopEmbeddings(),
    )
    target = BuildSubmissionTarget(builds, NoApprovedTags(), versions)
    canonical_draft_id = uuid.UUID("77777777-7777-4777-8777-777777777777")

    result = await target.create_or_get(_normalized_submission(account_id, canonical_draft_id, "Java 1.21.0"))

    persisted = await repository.get_by_id(result.build_id)
    assert persisted is not None
    assert persisted.versions == ["Java 1.21.0"]

    unknown_draft_id = uuid.UUID("88888888-8888-4888-8888-888888888888")
    with pytest.raises(ActionableSubmissionError) as error:
        await target.create_or_get(_normalized_submission(account_id, unknown_draft_id, "Java 1.21.99"))

    assert error.value.issues == (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
    assert await repository.get_by_source_submission_draft_id(unknown_draft_id) is None

    invalid_build = _build(BuildCategory.OTHER, account_id)
    invalid_build.versions = ["Java 1.21.99"]
    with pytest.raises(InvalidBuildError, match="Unknown canonical Minecraft versions"):
        await repository.save(invalid_build)
    assert invalid_build.id is None
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 1


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
                "(id, owner_account_id, schema_id, schema_revision, category, answers, origin, "
                "source_installation_id, expires_at) "
                "VALUES (:draft_id, :absorbed, 'redstone_squid.submission', 1, 'utility', '{}'::jsonb, "
                "'paper', :installation_id, now() + interval '7 days')"
            ),
            {"draft_id": draft_id, "absorbed": absorbed.id, "installation_id": installation_id},
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
        await session.execute(
            text(
                "INSERT INTO minecraft_paper_installations (id, owner_account_id, label, secret_hash) "
                "VALUES (:installation_id, :absorbed, 'Merge test', :secret_hash)"
            ),
            {
                "installation_id": installation_id,
                "absorbed": absorbed.id,
                "secret_hash": installation_secret_hash,
            },
        )
        await session.execute(
            text(
                "INSERT INTO minecraft_player_challenges "
                "(id, device_code_hash, user_code_hash, origin, java_uuid, installation_id, "
                "installation_credential_version, created_at, expires_at, approved_by_account_id, approved_at, "
                "exchanged_at) VALUES (:challenge_id, :device_hash, :user_hash, 'paper', :java_uuid, "
                ":installation_id, 1, now(), now() + interval '10 minutes', :absorbed, now(), now())"
            ),
            {
                "challenge_id": challenge_id,
                "device_hash": bytes.fromhex("22" * 32),
                "user_hash": bytes.fromhex("33" * 32),
                "java_uuid": java_uuid,
                "installation_id": installation_id,
                "absorbed": absorbed.id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO minecraft_player_grants "
                "(id, challenge_id, token_hash, account_id, java_uuid, origin, installation_id, "
                "installation_credential_version, issued_at, expires_at) VALUES "
                "(:grant_id, :challenge_id, :token_hash, :absorbed, :java_uuid, 'paper', :installation_id, 1, "
                "now(), now() + interval '5 minutes')"
            ),
            {
                "grant_id": grant_id,
                "challenge_id": challenge_id,
                "token_hash": bytes.fromhex("44" * 32),
                "absorbed": absorbed.id,
                "java_uuid": java_uuid,
                "installation_id": installation_id,
            },
        )

    await accounts.merge(survivor.id, absorbed.id)

    async with migrated_session_factory() as session:
        build_owner = await session.scalar(select(SQLBuild.submitter_account_id).where(SQLBuild.id == build_id))
        draft_owner = (
            await session.execute(
                text("SELECT owner_account_id, source_installation_id FROM submission_drafts WHERE id = :draft_id"),
                {"draft_id": draft_id},
            )
        ).one()
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
        installation = (
            await session.execute(
                text(
                    "SELECT owner_account_id, id, secret_hash FROM minecraft_paper_installations "
                    "WHERE id = :installation_id"
                ),
                {"installation_id": installation_id},
            )
        ).one()
        challenge = (
            await session.execute(
                text(
                    "SELECT approved_by_account_id, java_uuid, installation_id FROM minecraft_player_challenges "
                    "WHERE id = :challenge_id"
                ),
                {"challenge_id": challenge_id},
            )
        ).one()
        grant = (
            await session.execute(
                text("SELECT account_id, java_uuid, installation_id FROM minecraft_player_grants WHERE id = :grant_id"),
                {"grant_id": grant_id},
            )
        ).one()
        java_identity_owner = await session.scalar(
            text("SELECT account_id FROM account_identities WHERE provider = 'java' AND subject = :subject"),
            {"subject": str(java_uuid)},
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
        _normalized_submission(absorbed.id, pending_draft_id, "Java 1.21.0"),
        origin=SubmissionOrigin.PAPER,
        source_installation_id=installation_id,
    )
    claimed_payload = replace(
        _normalized_submission(absorbed.id, claimed_draft_id, "Java 1.21.0"),
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

    async with migrated_session_factory.begin() as session:
        build_id = (
            await session.execute(
                text(
                    "INSERT INTO builds (submission_status, category, submitter_account_id, "
                    "source_submission_draft_id, ai_generated, sponsor_installation_id, sponsor_display_name) "
                    "VALUES (0, 'Other', :absorbed, :draft_id, false, :installation_id, 'Merge-safe server') "
                    "RETURNING id"
                ),
                {
                    "absorbed": absorbed.id,
                    "draft_id": claimed_draft_id,
                    "installation_id": installation_id,
                },
            )
        ).scalar_one()
        await session.execute(text("INSERT INTO other_builds (build_id) VALUES (:build_id)"), {"build_id": build_id})

    await accounts.merge(survivor.id, absorbed.id)

    result = SubmissionTargetResult(
        build_id=build_id,
        target_key="postgres_builds",
        provenance={"source_draft_id": str(claimed_draft_id)},
    )
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
            text("SELECT status FROM submission_drafts WHERE id = :draft_id"),
            {"draft_id": pending_draft_id},
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
    assert pending_status == DraftStatus.PROCESSING.value


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
        _normalized_submission(absorbed.id, draft_id, "Java 1.21.0"),
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
