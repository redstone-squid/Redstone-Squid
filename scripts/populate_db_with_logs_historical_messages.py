"""Import historical build-log message bundles as pending AI-generated builds."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discord
from discord import Message, TextChannel

from squid.bootstrap import create_application_runtime
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.media import CatboxMirror, MediaMirror
from squid.bot.submission.message_context import BUILD_LOG_CHANNEL_IDS, group_messages
from squid.config import load_bot_process_config
from squid.runtime import ApplicationServices

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _GroupingMessage:
    message: Message
    id: int
    author_id: int
    created_at: datetime
    reference_id: int | None

    @classmethod
    def from_discord(cls, message: Message) -> "_GroupingMessage":
        """Expose Discord message facts to the framework-neutral grouper."""
        reference_id = message.reference.message_id if message.reference is not None else None
        return cls(message, message.id, message.author.id, message.created_at, reference_id)


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Counts produced while importing one or more channels."""

    groups: int = 0
    builds: int = 0
    imported: int = 0
    skipped_existing: int = 0
    ignored: int = 0
    failed: int = 0

    def __add__(self, other: "ImportSummary") -> "ImportSummary":
        return ImportSummary(
            groups=self.groups + other.groups,
            builds=self.builds + other.builds,
            imported=self.imported + other.imported,
            skipped_existing=self.skipped_existing + other.skipped_existing,
            ignored=self.ignored + other.ignored,
            failed=self.failed + other.failed,
        )


async def process_group(
    primary: Sequence[Message],
    context: Sequence[Message],
    services: ApplicationServices,
    *,
    model: str,
    reasoning_effort: str,
    mirror: MediaMirror,
    include_images: bool,
    dry_run: bool,
) -> ImportSummary:
    """Import one group without aborting the backfill on failure."""
    try:
        for message in primary:
            if await services.builds.list_ids_for_source_message(message.id):
                return ImportSummary(groups=1, skipped_existing=1)
        builds = await ingest_message_bundle(
            primary,
            context,
            services,
            model=model,
            reasoning_effort=reasoning_effort,
            mirror=mirror,
            include_images=include_images,
            dry_run=dry_run,
        )
    except Exception:
        logger.exception("Failed to import message group beginning at %s", primary[0].id)
        return ImportSummary(groups=1, failed=1)

    if not builds:
        return ImportSummary(groups=1, ignored=1)
    logger.info(
        "%s group %s as build(s) %s",
        "Inferred" if dry_run else "Imported",
        [message.id for message in primary],
        [build.id for build in builds],
    )
    return ImportSummary(groups=1, builds=len(builds), imported=0 if dry_run else len(builds))


async def process_channel(
    channel: TextChannel,
    services: ApplicationServices,
    *,
    model: str,
    reasoning_effort: str,
    mirror: MediaMirror,
    concurrency: int = 4,
    after: datetime | discord.Object | None = None,
    before: datetime | discord.Object | None = None,
    limit: int | None = None,
    group_window: float = 300,
    group_max_messages: int = 8,
    include_images: bool = True,
    dry_run: bool = False,
) -> ImportSummary:
    """Group chronological channel history and process it with bounded concurrency."""
    history = [
        message
        async for message in channel.history(limit=limit, oldest_first=True, after=after, before=before)
        if not message.author.bot
    ]
    wrapped = [_GroupingMessage.from_discord(message) for message in history]
    groups = group_messages(wrapped, window_seconds=group_window, max_messages=group_max_messages)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_group(index: int) -> ImportSummary:
        group = groups[index]
        primary = [item.message for item in group.primary]
        first_index = history.index(primary[0])
        lookback = history[max(0, first_index - 3) : first_index]
        context = [item.message for item in group.context]
        context.extend(message for message in lookback if message not in context)
        async with semaphore:
            return await process_group(
                primary,
                context,
                services,
                model=model,
                reasoning_effort=reasoning_effort,
                mirror=mirror,
                include_images=include_images,
                dry_run=dry_run,
            )

    summaries = await asyncio.gather(*(run_group(index) for index in range(len(groups))))
    summary = sum(summaries, start=ImportSummary())
    logger.info("Processed channel %s: %s", channel.id, summary)
    return summary


async def run_import(
    client: discord.Client,
    services: ApplicationServices,
    *,
    channel_ids: Sequence[int],
    model: str,
    reasoning_effort: str,
    mirror: MediaMirror,
    concurrency: int,
    after: datetime | discord.Object | None,
    before: datetime | discord.Object | None,
    limit: int | None,
    group_window: float,
    group_max_messages: int,
    include_images: bool,
    dry_run: bool,
) -> ImportSummary:
    """Wait for Discord, import the requested channels, and close the client."""
    try:
        await client.wait_until_ready()
        channels: list[TextChannel] = []
        for channel_id in channel_ids:
            channel = await client.fetch_channel(channel_id)
            if not isinstance(channel, TextChannel):
                msg = f"Discord channel {channel_id} is not a text channel."
                raise TypeError(msg)
            channels.append(channel)
        summaries = await asyncio.gather(
            *(
                process_channel(
                    channel,
                    services,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    mirror=mirror,
                    concurrency=concurrency,
                    after=after,
                    before=before,
                    limit=limit,
                    group_window=group_window,
                    group_max_messages=group_max_messages,
                    include_images=include_images,
                    dry_run=dry_run,
                )
                for channel in channels
            )
        )
        return sum(summaries, start=ImportSummary())
    finally:
        await client.close()


def _history_boundary(value: str | None) -> datetime | discord.Object | None:
    if value is None:
        return None
    if value.isdecimal():
        return discord.Object(id=int(value))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        msg = "ISO history boundaries must include a timezone"
        raise ValueError(msg)
    return parsed


async def main(arguments: argparse.Namespace) -> None:
    """Create process resources and run the historical import."""
    process_config = load_bot_process_config()
    intents = discord.Intents.default()
    intents.message_content = True
    model = arguments.model or process_config.openai.chat_model
    reasoning_effort = arguments.reasoning_effort or process_config.openai.reasoning_effort

    async with (
        create_application_runtime(process_config.runtime) as runtime,
        discord.Client(intents=intents) as client,
        asyncio.TaskGroup() as tasks,
    ):
        tasks.create_task(client.start(process_config.discord.token.get_secret_value()))
        import_task = tasks.create_task(
            run_import(
                client,
                runtime.services,
                channel_ids=arguments.channel_ids or BUILD_LOG_CHANNEL_IDS,
                model=model,
                reasoning_effort=reasoning_effort,
                mirror=CatboxMirror(process_config.catbox),
                concurrency=arguments.concurrency,
                after=_history_boundary(arguments.after),
                before=_history_boundary(arguments.before),
                limit=arguments.limit,
                group_window=arguments.group_window,
                group_max_messages=arguments.group_max_messages,
                include_images=not arguments.no_images,
                dry_run=arguments.dry_run,
            )
        )
    logger.info("Import complete: %s", import_task.result())


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id", dest="channel_ids", action="append", type=int)
    parser.add_argument("--model", help="Override the configured inference model.")
    parser.add_argument("--reasoning-effort", help="Override the configured reasoning effort.")
    parser.add_argument("--after", help="Oldest snowflake or ISO-8601 timestamp to include.")
    parser.add_argument("--before", help="Newest snowflake or ISO-8601 timestamp to include.")
    parser.add_argument("--limit", type=int, help="Maximum messages to read per channel.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--group-window", type=float, default=300)
    parser.add_argument("--group-max-messages", type=int, default=8)
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(parse_args()))
