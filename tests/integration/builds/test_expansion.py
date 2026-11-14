"""Expandable facts survive PostgreSQL storage, history, and retained drafts."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.history_models import BuildHistorySnapshot
from squid.builds.models import DoorBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.domain import BuildKind
from squid.records.infrastructure.repository import PostgresRecordRepository
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.infrastructure.repository import _to_model, _to_stored
from squid.submissions.models.entities import SubmissionDraft
from tests.support.build_edits import build_edit_session
from tests.support.expansion import expansion_document


async def test_expansion_round_trip_history_and_record_exclusion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    document = expansion_document()
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    async with SqlAlchemyUnitOfWork(sessions) as uow, uow.builds.editing(actor="test", source="test"):
        build = DoorBuild(
            submitter_account_id=account_id,
            submission_status=Status.CONFIRMED,
            expansion=document,
            orientation="Door",
            door_width=6,
            door_height=3,
            width=9,
            height=6,
            depth=2,
        )
        await uow.builds.save(build)
        await uow.commit()
        build_id = build.id
    async with sessions() as session:
        loaded = await session.get(DoorBuild, build_id)
        assert loaded is not None
        assert loaded.expansion == document
        snapshot = await session.scalar(select(BuildHistorySnapshot).where(BuildHistorySnapshot.build_id == build_id))
        assert snapshot is not None
        door = snapshot.snapshot["doors"]
        assert isinstance(door, dict)
        assert door["expansion"] == document.model_dump(mode="json")
        assert await PostgresRecordRepository(lambda: session).list_confirmed(BuildKind.DOOR) == ()
    async with SqlAlchemyUnitOfWork(sessions) as uow, uow.builds.editing(actor="test", source="test"):
        loaded = await uow.builds.get_for_update(build_id)
        assert isinstance(loaded, DoorBuild)
        loaded.expansion = None
        await uow.commit()
    async with sessions() as session:
        snapshots = list(await session.scalars(select(BuildHistorySnapshot).order_by(BuildHistorySnapshot.id)))
        assert len(snapshots) >= 2
        last_door = snapshots[-1].snapshot["doors"]
        assert isinstance(last_door, dict)
        assert last_door["expansion"] is None
        candidates = await PostgresRecordRepository(lambda: session).list_confirmed(BuildKind.DOOR)
        assert len(candidates) == 1
        assert candidates[0].candidate.fixed_volume == 108


async def test_retained_draft_document_round_trip(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    sessions = migrated_session_factory
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), account_id, "build_submission.v1", 6, "door"),
        origin=SubmissionOrigin.DISCORD,
        inferred=True,
        created_at=now,
        updated_at=now,
        expires_at=now.add(hours=24),
        door_expansion=expansion_document(),
    )
    async with sessions.begin() as session:
        session.add(_to_model(draft))
    async with sessions() as session:
        model = await session.get(SubmissionDraft, draft.snapshot.id)
        assert model is not None
        assert _to_stored(model).door_expansion == draft.door_expansion


@pytest.mark.parametrize("document", ["{}", "[]", "null", '{"schema_version":2}'])
async def test_database_rejects_invalid_document_envelope(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    document: str,
) -> None:
    sessions = migrated_session_factory
    async with build_edit_session(sessions) as session:
        account = Account()
        session.add(account)
        await session.flush()
        build = DoorBuild(submitter_account_id=account.id, submission_status=Status.PENDING)
        session.add(build)
        await session.flush()
        build_id = build.id
    with pytest.raises(IntegrityError, match="doors_expansion_version_check"):
        async with build_edit_session(sessions) as session:
            await session.execute(
                text("UPDATE doors SET expansion = CAST(:value AS jsonb) WHERE build_id = :id"),
                {"value": document, "id": build_id},
            )
