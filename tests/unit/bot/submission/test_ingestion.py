"""Inference intake delegates to persisted drafts without direct build writes."""

from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import discord
import pytest
from whenever import Instant

from squid.bot.submission import ingestion
from squid.builds.application.inference import BuildInferenceInput
from squid.builds.domain import BuildCategory, BuildDraft
from squid.core.actors import Actor
from squid.runtime import BotServices
from squid.submissions.application.drafts import DraftActor, StoredDraft
from squid.submissions.application.finalization import FinalizationJobSnapshot, SubmissionFinalizationService
from squid.submissions.application.inference_runs import InferenceCandidate, SubmissionInferenceRuns
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.finalization import FinalizationJobStatus
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid_ui_discord.testing import AsyncCallRecorder, MessageHarness
from tests.support.services import bot_services


class InferenceRecorder(SubmissionInferenceRuns):
    def __init__(self) -> None:
        self.infer_calls: AsyncCallRecorder = AsyncCallRecorder()
        self.resume_calls: AsyncCallRecorder = AsyncCallRecorder()

    async def infer(
        self,
        run_id: UUID,
        owner: int,
        bundle: BuildInferenceInput,
        *,
        model: str,
        reasoning_effort: str | None = None,
        source_files: tuple[SubmissionSourceFile, ...] = (),
        purpose: Literal["submission", "recalculation"] = "submission",
    ) -> tuple[InferenceCandidate, ...]:
        return await self.infer_calls(
            run_id,
            owner,
            bundle,
            model=model,
            reasoning_effort=reasoning_effort,
            source_files=source_files,
            purpose=purpose,
        )

    async def resume(self, run_id: UUID, actor: Actor) -> tuple[InferenceCandidate, ...] | None:
        return await self.resume_calls(run_id, actor)


class FinalizationRecorder(SubmissionFinalizationService):
    def __init__(self) -> None:
        self.submit_calls: AsyncCallRecorder = AsyncCallRecorder()

    async def submit(
        self,
        draft_id: UUID,
        account_id: DraftActor,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> FinalizationJobSnapshot:
        await self.submit_calls(draft_id, account_id, locale=locale)
        return FinalizationJobSnapshot(uuid4(), draft_id, 1, FinalizationJobStatus.PENDING, 0, now or Instant.now())


@dataclass(frozen=True, slots=True)
class Harness:
    """Checked services recording inference and finalization, plus ingestion helper boundaries."""

    services: BotServices
    inference: InferenceRecorder
    finalization: FinalizationRecorder
    materialize: AsyncCallRecorder
    download: AsyncCallRecorder


def setup(monkeypatch: pytest.MonkeyPatch, candidates: tuple[InferenceCandidate, ...], *, revision: int = 1) -> Harness:
    now = Instant.now()
    draft = StoredDraft(
        DraftSnapshot(uuid4(), 7, "build_submission.v1", 1, "door", revision=revision),
        SubmissionOrigin.DISCORD,
        now,
        now,
        now.add(days=7, days_assumed_24h_ok=True),
    )
    materialize = AsyncCallRecorder(result=draft)
    download = AsyncCallRecorder()
    monkeypatch.setattr(ingestion, "assemble_bundle", AsyncCallRecorder(result="input"))
    monkeypatch.setattr(ingestion, "get_or_create_discord_account_id", AsyncCallRecorder(result=7))
    monkeypatch.setattr(ingestion, "materialize_candidate", materialize)
    monkeypatch.setattr(ingestion, "receive_retained_file", download)
    inference = InferenceRecorder()
    inference.infer_calls.return_value = candidates
    inference.resume_calls.return_value = None
    finalization = FinalizationRecorder()
    # Accounts, drafts and forms only pass through to the patched helpers above, so they stay unconfigured.
    services = bot_services(submission_inference=inference, submission_finalization=finalization)
    return Harness(services, inference, finalization, materialize, download)


@dataclass(frozen=True)
class Attachment:
    id: int = 20
    filename: str = "image.png"
    content_type: str = "image/png"
    url: str = "https://cdn.discordapp.com/attachments/source.png"
    size: int = 4


def message(*, files: bool = False) -> discord.Message:
    source = MessageHarness(message_id=10).source
    source.author = object()
    source.attachments = []
    if files:
        source.attachments.append(Attachment())
    return cast(discord.Message, source)


def candidate() -> InferenceCandidate:
    return InferenceCandidate(uuid4(), uuid4(), 7, BuildDraft(category=BuildCategory.DOOR))


async def test_complete_candidate_uses_shared_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.materialize.assert_awaited_once()
    h.finalization.submit_calls.assert_awaited_once_with(item.id, 7, locale=None)


async def test_unknown_category_is_retained_without_inventing_a_door(monkeypatch: pytest.MonkeyPatch) -> None:
    item = replace(candidate(), facts=BuildDraft())
    h = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.materialize.assert_not_awaited()
    h.finalization.submit_calls.assert_not_awaited()


async def test_ambiguous_files_wait_for_explicit_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    h = setup(monkeypatch, (candidate(), candidate()))
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="test")
    assert h.materialize.await_count == 2
    h.download.assert_not_awaited()
    assert len(h.inference.infer_calls.await_args.kwargs["source_files"]) == 1


async def test_download_failure_reaches_preparation_as_a_retained_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://cdn.discordapp.com/source.png", 4)
    item = replace(candidate(), source_files=(source,))
    h = setup(monkeypatch, (item,))
    h.download.side_effect = OSError("unavailable")
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="test")
    h.finalization.submit_calls.assert_awaited_once()


async def test_redelivery_does_not_submit_a_corrected_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    h = setup(monkeypatch, (candidate(),), revision=2)
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.download.assert_not_awaited()
    h.finalization.submit_calls.assert_not_awaited()


async def test_replayed_delivery_uses_retained_candidates_without_reading_changed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,), revision=2)
    h.inference.resume_calls.return_value = (item,)
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="changed-model")
    cast(Any, ingestion.assemble_bundle).assert_not_awaited()
    h.inference.infer_calls.assert_not_awaited()


async def test_staff_retry_is_attributed_to_the_staff_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,))
    actor = Actor(account_id=19)

    await ingestion.admit_candidates(h.services, (item,), actor=actor)

    assert h.materialize.await_args is not None
    assert h.materialize.await_args.kwargs["actor"] == actor
    h.finalization.submit_calls.assert_awaited_once_with(item.id, actor, locale=None)
