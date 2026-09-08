"""Inference intake delegates to persisted drafts without direct build writes."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import discord
import pytest
from whenever import Instant

from squid.bot.submission import ingestion
from squid.builds.domain import BuildCategory, BuildDraft
from squid.runtime import BotServices
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.inference_runs import InferenceCandidate
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.source_files import SubmissionSourceFile


def setup(
    monkeypatch: pytest.MonkeyPatch, candidates: tuple[InferenceCandidate, ...], *, revision: int = 1
) -> tuple[BotServices, Any, Any]:
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
    services = SimpleNamespace(
        accounts=object(),
        submission_inference=SimpleNamespace(
            infer=AsyncMock(return_value=candidates), resume=AsyncMock(return_value=None)
        ),
        submission_drafts=object(),
        submission_forms=object(),
        submission_finalization=SimpleNamespace(submit=AsyncMock()),
    )
    return cast(BotServices, services), materialize, download


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
    services, materialize, _ = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], services, model="test")
    materialize.assert_awaited_once()
    cast(Any, services.submission_finalization.submit).assert_awaited_once_with(item.id, 7, locale=None)


async def test_unknown_category_is_retained_without_inventing_a_door(monkeypatch: pytest.MonkeyPatch) -> None:
    item = replace(candidate(), facts=BuildDraft())
    services, materialize, _ = setup(monkeypatch, (item,))
    await ingestion.ingest_message_bundle([message()], [], services, model="test")
    materialize.assert_not_awaited()
    cast(Any, services.submission_finalization.submit).assert_not_awaited()


async def test_ambiguous_files_wait_for_explicit_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    services, materialize, download = setup(monkeypatch, (candidate(), candidate()))
    await ingestion.ingest_message_bundle([message(files=True)], [], services, model="test")
    assert materialize.await_count == 2
    download.assert_not_awaited()
    assert len(cast(Any, services.submission_inference.infer).await_args.kwargs["source_files"]) == 1


async def test_download_failure_reaches_preparation_as_a_retained_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://cdn.discordapp.com/source.png", 4)
    item = replace(candidate(), source_files=(source,))
    services, _, download = setup(monkeypatch, (item,))
    download.side_effect = OSError("unavailable")
    await ingestion.ingest_message_bundle([message(files=True)], [], services, model="test")
    cast(Any, services.submission_finalization.submit).assert_awaited_once()


async def test_redelivery_does_not_submit_a_corrected_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _, download = setup(monkeypatch, (candidate(),), revision=2)
    await ingestion.ingest_message_bundle([message()], [], services, model="test")
    download.assert_not_awaited()
    cast(Any, services.submission_finalization.submit).assert_not_awaited()


async def test_replayed_delivery_uses_retained_candidates_without_reading_changed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = candidate()
    services, _, _ = setup(monkeypatch, (item,), revision=2)
    cast(Any, services.submission_inference.resume).return_value = (item,)
    await ingestion.ingest_message_bundle([message(files=True)], [], services, model="changed-model")
    cast(Any, ingestion.assemble_bundle).assert_not_awaited()
    cast(Any, services.submission_inference.infer).assert_not_awaited()
