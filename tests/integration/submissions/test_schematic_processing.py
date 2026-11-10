"""Original uploads survive durable analysis and become build attachments atomically."""

import base64
import hashlib
from dataclasses import replace
from functools import partial
from pathlib import Path

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.bootstrap import create_schematic_service
from squid.config import SchematicConfig
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.application import IngestedSchematic, IngestRequest
from squid.schematics.application.jobs import SchematicJobService
from squid.schematics.application.storage import SchematicStorageService
from squid.schematics.errors import InvalidSchematicError
from squid.schematics.infrastructure.durable import SchematicJobRunner
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.schematics.models import BuildSchematic, SchematicRenderQueueItem
from squid.submissions.application import CheckedInFormManifestRegistry, SubmissionDraftService
from squid.submissions.application.commit import BuildSubmissionExecutor
from squid.submissions.application.finalization import ClaimedFinalizationJob
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.finalization import (
    FinalizedBuild,
    SchematicArtifactState,
    SchematicRightsPolicy,
    SubmissionSchematicLicense,
    SubmissionSchematicVisibility,
)
from squid.submissions.domain.schematics import DraftSchematicState
from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.models.schematics import DraftSchematicSource
from tests.integration.submissions.test_commit import PreparedBuilds
from tests.integration.submissions.test_schematics import create_draft, reload
from tests.support.submission_targets import normalized_submission, seed_account_and_version
from tests.support.unit_of_work import transact
from tests.unit.schematics.fakes import make_analysis


@pytest.mark.schematic
@pytest.mark.parametrize("public", [False, True])
async def test_original_upload_reaches_build_and_preview_queue(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    public: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = pytest.importorskip("nucleation")
    schematic = native.Schematic.create("original contents")
    schematic.set_block(0, 0, 0, "minecraft:stone")
    data = base64.b64decode(schematic.to_schematic_b64())
    sessions = migrated_session_factory
    uows = partial(SqlAlchemyUnitOfWork, sessions)
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    artifacts = LocalArtifactStore(tmp_path / "objects")
    jobs = SchematicJobService(uow_factory=uows)
    config = SchematicConfig(job_poll_interval_seconds=0.01, job_wait_timeout_seconds=10)
    schematics = create_schematic_service(config, artifacts, jobs, uow_factory=uows)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=uows)
    uploads = DraftSchematicService(drafts, artifacts, uow_factory=uows, ingestor=schematics)
    source = await uploads.upload(draft.snapshot.id, owner, filename="original.schem", data=data)
    assert source.state is DraftSchematicState.PENDING
    assert (await uploads.read_for_draft(draft.snapshot.id)).state is SchematicArtifactState.PROCESSING

    # A fresh service instance processes the retained work after the uploader goes away.
    uploads = DraftSchematicService(drafts, artifacts, uow_factory=uows, ingestor=schematics)
    async with SchematicWorkerPool(config).running() as analyzer:
        runner = SchematicJobRunner(jobs, artifacts, analyzer, config)

        async def pump() -> None:
            while True:
                await runner.process_batch()
                await anyio.sleep(0.01)

        with anyio.fail_after(20):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(pump)
                await uploads.process_pending()
                tasks.cancel_scope.cancel()
    ready = await uploads.read_for_draft(draft.snapshot.id)
    assert ready.artifact_id == source.id
    assert ready.state is SchematicArtifactState.READY
    assert await artifacts.get(source.object_key, max_bytes=len(data)) == data
    assert await SchematicStorageService(artifacts, uow_factory=uows).get_file(source.sha256 or "") == data
    # Replaying the same upload must preserve completed analysis.
    assert (
        await uploads.upload(draft.snapshot.id, owner, filename=source.filename, data=data, upload_id=source.id)
    ).state is DraftSchematicState.READY

    payload = normalized_submission(owner, draft.snapshot.id, "Java 1.21.0")
    payload = replace(payload, artifacts=replace(payload.artifacts, schematic_id=source.id))
    if public:
        payload = replace(
            payload,
            schematic_policy=SchematicRightsPolicy(
                SubmissionSchematicVisibility.PUBLIC_DOWNLOAD,
                SubmissionSchematicLicense.CC_BY_4_0,
                rights_attested=True,
            ),
        )
    current = await reload(sessions, draft)
    now = Instant.now()
    await transact(
        uows,
        lambda uow: uow.finalization_jobs.enqueue(current, payload, now=now, expires_at=current.expires_at),
        commit=True,
    )
    (claim,) = await transact(uows, lambda uow: uow.finalization_jobs.claim(now=now, limit=1), commit=True)
    executor = BuildSubmissionExecutor(PreparedBuilds(), uow_factory=uows)

    async def fail_receipt(
        self: PostgresFinalizationJobRepository, job: ClaimedFinalizationJob, result: FinalizedBuild, *, now: Instant
    ) -> bool:
        raise RuntimeError("receipt write failed")

    with monkeypatch.context() as patch:
        patch.setattr(PostgresFinalizationJobRepository, "complete", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt write failed"):
            await executor.execute(claim, now=now)
    async with sessions() as session:
        assert (await session.scalars(select(BuildSchematic))).all() == []
        assert (await session.scalars(select(SchematicRenderQueueItem))).all() == []
    result = await executor.execute(claim, now=now)
    assert result is not None
    async with sessions() as session:
        attachment = (await session.scalars(select(BuildSchematic))).one()
        assert attachment.file_sha256 == source.sha256
        assert attachment.original_filename == "original.schem"
        assert attachment.uploaded_by_account_id == owner
        assert attachment.is_primary
        assert attachment.visibility == payload.schematic_policy.visibility.value
        assert (attachment.rights_attested_at is not None) is public
        assert (attachment.published_at is not None) is public
        assert (await session.scalars(select(SchematicRenderQueueItem.build_id))).all() == [attachment.build_id]
    # Replayed claims cannot create duplicate attachments or receipts.
    assert await executor.execute(claim, now=now) is None


class ControlledIngestor:
    """Fail one source while allowing other durable work to progress."""

    def __init__(self, failure: Exception | None) -> None:
        self.failure = failure
        self.calls = 0

    async def ingest(self, request: IngestRequest) -> IngestedSchematic:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        digest = hashlib.sha256(request.data).hexdigest()
        return IngestedSchematic(digest, make_analysis(sha256=digest))


@pytest.mark.parametrize("invalid", [False, True])
async def test_processing_rejects_invalid_files_but_retries_infrastructure_failures(
    migrated_session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, invalid: bool
) -> None:
    sessions = migrated_session_factory
    uows = partial(SqlAlchemyUnitOfWork, sessions)
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    artifacts = LocalArtifactStore(tmp_path)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=uows)
    failure = InvalidSchematicError("Invalid NBT") if invalid else RuntimeError("storage temporarily unavailable")
    ingestor = ControlledIngestor(failure)
    uploads = DraftSchematicService(drafts, artifacts, uow_factory=uows, ingestor=ingestor)
    source = await uploads.upload(draft.snapshot.id, owner, filename="source.schem", data=b"original bytes")
    await uploads.process_pending()
    state = (await uploads.list(draft.snapshot.id, owner))[0].state
    assert state is (DraftSchematicState.REJECTED if invalid else DraftSchematicState.PENDING)
    async with sessions() as session:
        stored = await session.get(DraftSchematicSource, source.id)
        assert stored is not None
        assert stored.last_error
    ingestor.failure = None
    await uploads.process_pending()
    assert ingestor.calls == (1 if invalid else 2)
    ready = await uploads.read_for_draft(draft.snapshot.id)
    assert ready.state is (SchematicArtifactState.REJECTED if invalid else SchematicArtifactState.READY)


async def test_late_analysis_cannot_resurrect_a_deleted_source(
    migrated_session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    sessions = migrated_session_factory
    uows = partial(SqlAlchemyUnitOfWork, sessions)
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    artifacts = LocalArtifactStore(tmp_path)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=uows)
    uploads = DraftSchematicService(drafts, artifacts, uow_factory=uows)
    source = await uploads.upload(draft.snapshot.id, owner, filename="source.schem", data=b"original bytes")
    await uploads.discard(draft.snapshot.id, owner, source.id)
    async with sessions.begin() as session:
        await session.execute(update(DraftSchematicSource).values(updated_at=Instant.now().subtract(minutes=10)))
    await uploads.cleanup()
    assert source.sha256 is not None
    await transact(
        uows,
        lambda uow: uow.draft_schematics.finish_analysis(
            source, IngestedSchematic(source.sha256, make_analysis(sha256=source.sha256))
        ),
        commit=True,
    )
    async with sessions() as session:
        stored = await session.get(DraftSchematicSource, source.id)
        assert stored is not None
        assert stored.state is DraftSchematicState.DELETED
        assert stored.analysis is None
