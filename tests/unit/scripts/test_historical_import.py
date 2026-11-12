"""Historical imports preserve authors, bound work, and keep previews read-only."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
import discord
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


async def test_worker_pool_bounds_concurrency_and_continues_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    active = peak = calls = 0

    async def ingest(*args: object, **kwargs: object) -> object:
        nonlocal active, peak, calls
        calls += 1
        call = calls
        active += 1
        peak = max(peak, active)
        await anyio.sleep(0)
        active -= 1
        if call == 1:
            raise OSError
        return uuid4()

    monkeypatch.setattr(importer, "ingest_message_bundle", ingest)
    channel = Channel([message(i, i) for i in range(1, 21)])
    result = await importer.process_channel(
        cast(discord.TextChannel, channel), services(), importer.ImportOptions("x", concurrency=2)
    )
    assert result.groups == 20
    assert result.processed + result.failed == 20
    assert result.failed == 1
    assert peak == 2


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
