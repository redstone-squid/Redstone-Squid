"""Submission progress participates in durable Discord reconciliation."""

from dataclasses import replace
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application.inference import BuildInferenceInput
from squid.builds.domain import BuildCategory, BuildDraft
from squid.core.errors import JSONValue
from squid.submissions.application.inference_runs import candidate_id
from squid.submissions.domain import DraftStatus
from squid.submissions.infrastructure.inference_runs import PostgresInferenceRuns
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from squid.sync.infrastructure.models import DiscordSyncQueueItem
from tests.integration.submissions.test_schematics import create_draft
from tests.support.submission_targets import seed_account_and_version


async def test_run_and_draft_changes_enqueue_minimal_status(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    run = uuid4()
    bundle = BuildInferenceInput.from_single_message(
        author_name="owner",
        content="private original facts",
        message_id=123,
        author_id=456,
        channel_id=789,
        server_id=987,
    )
    inputs: dict[str, JSONValue] = {
        "purpose": "submission",
        "bundle": TypeAdapter(BuildInferenceInput).dump_python(bundle, mode="json"),
    }
    runs = PostgresInferenceRuns(sessions)
    claim = await runs.begin(run, owner, inputs, capacity=1000)
    assert claim.token is not None
    await runs.complete(run, claim.token, (BuildDraft(category=BuildCategory.OTHER),))
    initial = await create_draft(sessions, owner)
    draft = replace(initial, snapshot=replace(initial.snapshot, id=candidate_id(run, 0)), inference_run_id=run)
    await PostgresDraftRepository(sessions).create(draft)
    async with sessions.begin() as session:
        row = await session.get(SubmissionDraft, draft.snapshot.id)
        assert row is not None
        row.status = DraftStatus.NEEDS_ATTENTION
    status = await runs.public_status(run)
    assert status is not None
    assert status.candidates == ("needs attention",)
    assert status.source_message_id == 123
    assert "private original facts" not in repr(status)
    async with sessions() as session:
        kinds = set(
            await session.scalars(
                select(DiscordSyncQueueItem.resource_kind).where(
                    DiscordSyncQueueItem.source_key.in_((str(run), str(draft.snapshot.id)))
                )
            )
        )
    assert kinds == {"inference_run", "submission_draft"}
