"""Historical imports preserve authors, bound work, and keep previews read-only."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
import discord
import httpx2
import openai
import pytest

from scripts import populate_db_with_logs_historical_messages as importer
from squid.runtime import BotServices


def message(identifier: int, author: int = 1, *, bot: bool = False, reply: int | None = None) -> discord.Message:
    return cast(
        discord.Message,
        SimpleNamespace(
            id=identifier,
            author=SimpleNamespace(id=author, bot=bot),
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=identifier),
            reference=SimpleNamespace(message_id=reply) if reply is not None else None,
        ),
    )


class Channel:
    id = 42

    def __init__(self, messages: list[discord.Message]) -> None:
        self.messages = messages
        self.history_options: dict[str, object] = {}

    async def history(self, **kwargs: object) -> AsyncIterator[discord.Message]:
        self.history_options = kwargs
        for item in self.messages:
            yield item


def services(*, consent: bool = True) -> BotServices:
    return cast(
        BotServices,
        SimpleNamespace(
            accounts=SimpleNamespace(
                get_account_by_identity=AsyncMock(
                    return_value=SimpleNamespace(id=7, needs_consent_refresh=False) if consent else None
                )
            ),
            submission_inference=SimpleNamespace(public_status=AsyncMock(return_value=None)),
            builds=SimpleNamespace(list_ids_for_source_message=AsyncMock(return_value=[])),
            build_inference=SimpleNamespace(infer=AsyncMock(return_value=[object(), object()])),
        ),
    )


async def test_grouping_preserves_every_author_and_context() -> None:
    history = [message(1), message(2), message(3, 2), message(4), message(5, 99, bot=True), message(6)]
    channel = Channel(history)
    bundles = [
        item async for item in importer.iter_bundles(cast(discord.TextChannel, channel), importer.ImportOptions("x"))
    ]
    assert [[item.id for item in bundle.primary] for bundle in bundles] == [[1, 2], [3], [4], [6]]
    assert [item.id for item in bundles[-1].preceding] == [3, 4, 5]


async def test_grouping_obeys_reply_gap_and_count_limits() -> None:
    channel = Channel([message(1), message(2), message(3), message(4, reply=1), message(20)])
    options = importer.ImportOptions("x", group_max_messages=2, group_window=5, limit=10)
    bundles = [item async for item in importer.iter_bundles(cast(discord.TextChannel, channel), options)]
    assert [[item.id for item in bundle.primary] for bundle in bundles] == [[1, 2], [3], [4], [20]]
    assert channel.history_options == {"limit": 10, "oldest_first": True, "after": None, "before": None}


async def test_dry_run_only_calls_unretained_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    assemble = AsyncMock(return_value=object())
    ingest = AsyncMock()
    monkeypatch.setattr(importer, "assemble_bundle", assemble)
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    result = await importer.process_group(
        importer.MessageBundle((message(1),), ()), services(), importer.ImportOptions("x", dry_run=True)
    )
    assert result.inferred_candidates == 2
    assert result.processed == 1
    ingest.assert_not_awaited()
    assemble.assert_awaited_once()


async def test_unconsented_author_is_skipped_before_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    ingest = AsyncMock()
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    result = await importer.process_group(
        importer.MessageBundle((message(1),), ()), services(consent=False), importer.ImportOptions("x")
    )
    assert result.skipped_consent == 1
    ingest.assert_not_awaited()


async def test_real_import_reuses_shared_resumable_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    ingest = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    bundle = importer.MessageBundle((message(1),), ())
    runtime = services()
    result = await importer.process_group(bundle, runtime, importer.ImportOptions("x", include_images=False))
    assert result.processed == 1
    ingest.assert_awaited_once_with(
        bundle.primary, bundle.preceding, runtime, model="x", reasoning_effort=None, include_images=False
    )


@pytest.mark.parametrize(("existing", "skipped", "failed"), [([True, True], 1, 0), ([True, False], 0, 1)])
async def test_existing_builds_do_not_create_new_bundle_identities(
    monkeypatch: pytest.MonkeyPatch, existing: list[bool], skipped: int, failed: int
) -> None:
    runtime = services()
    monkeypatch.setattr(runtime.builds, "list_ids_for_source_message", AsyncMock(side_effect=existing))
    ingest = AsyncMock()
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    result = await importer.process_group(
        importer.MessageBundle((message(1), message(2)), ()), runtime, importer.ImportOptions("x")
    )
    assert (result.skipped_existing, result.failed) == (skipped, failed)
    ingest.assert_not_awaited()


async def test_worker_pool_drains_in_flight_work_and_reports_earliest_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    later_failed = asyncio.Event()
    calls: list[int] = []

    async def process(
        bundle: importer.MessageBundle, runtime: BotServices, options: importer.ImportOptions
    ) -> importer.ImportSummary:
        identifier = bundle.primary[0].id
        calls.append(identifier)
        if identifier == 1:
            await later_failed.wait()
            await anyio.sleep(0)
        else:
            later_failed.set()
        return importer.ImportSummary(groups=1, failed=1)

    monkeypatch.setattr(importer, "process_group", process)
    channel = Channel([message(i, i) for i in range(1, 21)])
    with pytest.raises(importer.ImportStopped) as stopped:
        await importer.process_channel(
            cast(discord.TextChannel, channel), services(), importer.ImportOptions("x", concurrency=2)
        )
    assert calls == [1, 2]
    assert stopped.value.message_id == 1
    assert stopped.value.channel_id == 42
    assert stopped.value.summary.failed == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["--concurrency", "0"],
        ["--limit", "-1"],
        ["--group-max-messages", "0"],
        ["--group-window", "nan"],
        ["--group-window", "-1"],
        ["--after", "2026-01-01"],
        ["--after", "2026-02-01T00:00:00Z", "--before", "2026-01-01T00:00:00Z"],
    ],
)
def test_invalid_arguments_fail_before_connecting(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        importer.parse_args(arguments)


async def test_retained_run_resumes_even_when_a_candidate_is_already_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = services()
    monkeypatch.setattr(runtime.submission_inference, "public_status", AsyncMock(return_value=object()))
    lookup = AsyncMock(return_value=[42])
    monkeypatch.setattr(runtime.builds, "list_ids_for_source_message", lookup)
    ingest = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    result = await importer.process_group(
        importer.MessageBundle((message(1), message(2)), ()), runtime, importer.ImportOptions("x")
    )
    assert result.processed == 1
    lookup.assert_not_awaited()
    ingest.assert_awaited_once()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_channel_consent_allows_import_without_account_consent(
    monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    runtime = services(consent=False)
    lookup = AsyncMock(side_effect=AssertionError("Channel consent must not read or update account consent"))
    monkeypatch.setattr(runtime.accounts, "get_account_by_identity", lookup)
    assemble = AsyncMock(return_value=object())
    ingest = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(importer, "assemble_bundle", assemble)
    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    arguments = importer.parse_args(["--channel-consent"])
    options = importer.ImportOptions("x", dry_run=dry_run, channel_consent=arguments.channel_consent)

    result = await importer.process_group(importer.MessageBundle((message(1),), ()), runtime, options)

    assert result.processed == 1
    assert result.skipped_consent == 0
    lookup.assert_not_awaited()
    if dry_run:
        assert result.inferred_candidates == 2
        assemble.assert_awaited_once()
        ingest.assert_not_awaited()
    else:
        ingest.assert_awaited_once()
        assemble.assert_not_awaited()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_credit_exhaustion_stops_before_later_bundles(monkeypatch: pytest.MonkeyPatch, dry_run: bool) -> None:
    response = httpx2.Response(402, request=httpx2.Request("POST", "https://example.invalid/chat"))
    error = openai.APIStatusError("Insufficient credits", response=response, body={"code": 402})
    infer = AsyncMock(side_effect=error)
    runtime = services()
    monkeypatch.setattr(importer, "assemble_bundle", AsyncMock(return_value=object()))
    monkeypatch.setattr(importer, "ingest_message_bundle", infer)
    monkeypatch.setattr(runtime.build_inference, "infer", infer)
    channel = Channel([message(i, i) for i in range(1, 10)])
    with pytest.raises(importer.ImportStopped) as stopped:
        await importer.process_channel(
            cast(discord.TextChannel, channel), runtime, importer.ImportOptions("x", concurrency=1, dry_run=dry_run)
        )
    assert stopped.value.message_id == 1
    infer.assert_awaited_once()


async def test_resume_keeps_original_grouping_context_and_history_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = Channel([message(1), message(2), message(3, 2), message(4, 2), message(5)])
    processed: list[importer.MessageBundle] = []

    async def process(
        bundle: importer.MessageBundle, runtime: BotServices, options: importer.ImportOptions
    ) -> importer.ImportSummary:
        processed.append(bundle)
        return importer.ImportSummary(groups=1, processed=1)

    monkeypatch.setattr(importer, "process_group", process)
    options = importer.ImportOptions("x", resume_channel=42, resume_from=3, limit=5)
    await importer.process_channel(cast(discord.TextChannel, channel), services(), options)
    assert [[item.id for item in bundle.primary] for bundle in processed] == [[3, 4], [5]]
    assert [item.id for item in processed[0].preceding] == [1, 2]
    assert channel.history_options["limit"] == 5
    assert channel.history_options["after"] is None


@pytest.mark.parametrize("resume_from", [2, 99])
async def test_resume_rejects_missing_or_changed_bundle_boundary(
    monkeypatch: pytest.MonkeyPatch, resume_from: int
) -> None:
    process = AsyncMock()
    monkeypatch.setattr(importer, "process_group", process)
    channel = Channel([message(1), message(2), message(3, 2)])
    with pytest.raises(ValueError, match="Resume message"):
        await importer.process_channel(
            cast(discord.TextChannel, channel),
            services(),
            importer.ImportOptions("x", resume_channel=42, resume_from=resume_from),
        )
    process.assert_not_awaited()


@pytest.mark.parametrize("arguments", [["--resume-channel", "42"], ["--resume-from", "3"]])
def test_resume_requires_both_channel_and_message(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        importer.parse_args(arguments)


async def test_resume_skips_completed_channels_and_continues_to_later_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer.discord, "TextChannel", Channel)
    channels = {identifier: Channel([]) for identifier in [41, 42, 43]}
    for identifier, channel in channels.items():
        channel.id = identifier
    fetch = AsyncMock(side_effect=lambda identifier: channels[identifier])
    client = cast(discord.Client, SimpleNamespace(fetch_channel=fetch))
    process = AsyncMock(return_value=importer.ImportSummary(groups=1, processed=1))
    monkeypatch.setattr(importer, "process_channel", process)

    result = await importer.run_import(
        client, services(), [41, 42, 43], importer.ImportOptions("x", resume_channel=42, resume_from=3)
    )

    assert [call.args[0] for call in fetch.await_args_list] == [42, 43]
    assert result.processed == 2


async def test_channel_failure_stops_later_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer.discord, "TextChannel", Channel)
    channel = Channel([])
    fetch = AsyncMock(return_value=channel)
    client = cast(discord.Client, SimpleNamespace(fetch_channel=fetch))
    stopped = importer.ImportStopped(42, 3, importer.ImportSummary(groups=1, failed=1))
    monkeypatch.setattr(importer, "process_channel", AsyncMock(side_effect=stopped))

    with pytest.raises(importer.ImportStopped):
        await importer.run_import(client, services(), [42, 43], importer.ImportOptions("x"))

    fetch.assert_awaited_once_with(42)


def test_resume_log_reports_inclusive_message_and_original_upper_bound(caplog: pytest.LogCaptureFixture) -> None:
    importer.ImportStopped(42, 3, importer.ImportSummary(failed=1)).log_resume("2026-11-12T10:00:00+00:00")

    assert "--before 2026-11-12T10:00:00+00:00 --resume-channel 42 --resume-from 3" in caplog.text
    assert "Keep --after, --limit, channel order, and grouping unchanged" in caplog.text
