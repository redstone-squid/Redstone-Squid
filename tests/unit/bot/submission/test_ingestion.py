"""Inference intake delegates to persisted drafts without direct build writes."""

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, create_autospec
from uuid import uuid4

import discord
import pytest
from whenever import Instant

from squid.bot.submission import ingestion
from squid.builds.domain import BuildCategory, BuildDraft
from squid.permissions.domain import Subject
from squid.runtime import BotServices
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.finalization import SubmissionFinalizationService
from squid.submissions.application.inference_runs import InferenceCandidate, SubmissionInferenceRuns
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.source_files import SubmissionSourceFile
from tests.support.services import bot_services


@dataclass(frozen=True, slots=True)
class Harness:
    """Bot services with autospecced inference and finalization, plus the patched ingestion helpers."""

    services: BotServices
    inference: Any
    finalization: Any
    materialize: AsyncMock
    download: AsyncMock


def setup(monkeypatch: pytest.MonkeyPatch, candidates: tuple[InferenceCandidate, ...], *, revision: int = 1) -> Harness:
    now = Instant.now()
    draft = StoredDraft(
        DraftSnapshot(uuid4(), 7, "build_submission.v1", 1, "door", revision=revision),
        SubmissionOrigin.DISCORD,
        now,
        now,
        now.add(days=7, days_assumed_24h_ok=True),
    )
    materialize = AsyncMock(return_value=draft)
    download = AsyncMock()
    monkeypatch.setattr(ingestion, "assemble_bundle", AsyncMock(return_value="input"))
    monkeypatch.setattr(ingestion, "account_id_for", AsyncMock(return_value=7))
    monkeypatch.setattr(ingestion, "materialize_candidate", materialize)
    monkeypatch.setattr(ingestion, "receive_retained_file", download)
    inference = create_autospec(SubmissionInferenceRuns, instance=True, spec_set=True)
    inference.infer.return_value = candidates
    inference.resume.return_value = None
    finalization = create_autospec(SubmissionFinalizationService, instance=True, spec_set=True)
    # Accounts, drafts and forms only pass through to the patched helpers above, so they stay unconfigured.
    services = bot_services(submission_inference=inference, submission_finalization=finalization)
    return Harness(services, inference, finalization, materialize, download)


def message(*, files: bool = False) -> discord.Message:
    attachments = (
        [
            SimpleNamespace(
                id=20,
                filename="image.png",
                content_type="image/png",
                url="https://cdn.discordapp.com/attachments/source.png",
                size=4,
            )
        ]
        if files
        else []
    )
    return cast(discord.Message, SimpleNamespace(id=10, author=object(), attachments=attachments))


def candidate() -> InferenceCandidate:
    return InferenceCandidate(uuid4(), uuid4(), 7, BuildDraft(category=BuildCategory.DOOR))


async def test_complete_candidate_uses_shared_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.materialize.assert_awaited_once()
    h.finalization.submit.assert_awaited_once_with(item.id, 7, locale=None)


async def test_unknown_category_is_retained_without_inventing_a_door(monkeypatch: pytest.MonkeyPatch) -> None:
    item = replace(candidate(), facts=BuildDraft())
    h = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.materialize.assert_not_awaited()
    h.finalization.submit.assert_not_awaited()


async def test_ambiguous_files_wait_for_explicit_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    h = setup(monkeypatch, (candidate(), candidate()))
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="test")
    assert h.materialize.await_count == 2
    h.download.assert_not_awaited()
    assert len(h.inference.infer.await_args.kwargs["source_files"]) == 1


async def test_download_failure_reaches_preparation_as_a_retained_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://cdn.discordapp.com/source.png", 4)
    item = replace(candidate(), source_files=(source,))
    h = setup(monkeypatch, (item,))
    h.download.side_effect = OSError("unavailable")
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="test")
    h.finalization.submit.assert_awaited_once()


async def test_redelivery_does_not_submit_a_corrected_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    h = setup(monkeypatch, (candidate(),), revision=2)
    await ingestion.ingest_message_bundle([message()], [], h.services, model="test")
    h.download.assert_not_awaited()
    h.finalization.submit.assert_not_awaited()


async def test_replayed_delivery_uses_retained_candidates_without_reading_changed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,), revision=2)
    h.inference.resume.return_value = (item,)
    await ingestion.ingest_message_bundle([message(files=True)], [], h.services, model="changed-model")
    cast(Any, ingestion.assemble_bundle).assert_not_awaited()
    h.inference.infer.assert_not_awaited()


async def test_staff_retry_is_attributed_to_the_staff_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    item = candidate()
    h = setup(monkeypatch, (item,))
    actor = Subject(account_id=19)

    await ingestion.admit_candidates(h.services, (item,), actor=actor)

    assert h.materialize.await_args is not None
    assert h.materialize.await_args.kwargs["actor"] == actor
    h.finalization.submit.assert_awaited_once_with(item.id, actor, locale=None)
