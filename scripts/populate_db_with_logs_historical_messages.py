"""Import historical build-log messages as pending AI-generated builds."""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discord
from discord import Message, TextChannel
from dotenv import load_dotenv

load_dotenv()

from squid.bootstrap import create_application_runtime  # noqa: E402
from squid.builds.application import BuildInferenceInput  # noqa: E402
from squid.config import BotProcessConfig  # noqa: E402
from squid.runtime import ApplicationServices  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_IDS = (726156829629087814, 667401499554611210)
DEFAULT_MODEL = "gpt-4.1-nano"


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Counts produced while importing one or more channels."""

    imported: int = 0
    existing: int = 0
    ignored: int = 0
    failed: int = 0

    def __add__(self, other: "ImportSummary") -> "ImportSummary":
        return ImportSummary(
            imported=self.imported + other.imported,
            existing=self.existing + other.existing,
            ignored=self.ignored + other.ignored,
            failed=self.failed + other.failed,
        )


async def process_channel(
    channel: TextChannel,
    services: ApplicationServices,
    *,
    model: str,
) -> ImportSummary:
    """Infer and persist every eligible historical message in a channel."""
    summary = ImportSummary()
    async for message in channel.history(limit=None, oldest_first=True):
        result = await process_message(message, services, model=model)
        summary += result
    logger.info(
        "Processed channel %s: %d imported, %d existing, %d ignored, %d failed",
        channel.id,
        summary.imported,
        summary.existing,
        summary.ignored,
        summary.failed,
    )
    return summary


async def process_message(
    message: Message,
    services: ApplicationServices,
    *,
    model: str,
) -> ImportSummary:
    """Import one Discord message without aborting the backfill on failure."""
    if message.author.bot:
        return ImportSummary(ignored=1)

    try:
        tracked = await services.messages.get(message.id)
        if tracked is not None and tracked.build_id is not None:
            return ImportSummary(existing=1)

        build = await services.build_inference.infer(
            BuildInferenceInput(
                author_name=message.author.display_name,
                content=message.clean_content,
                message_id=message.id,
                author_id=message.author.id,
                channel_id=message.channel.id,
                server_id=message.guild.id if message.guild is not None else None,
            ),
            model=model,
        )
        if build is None:
            return ImportSummary(ignored=1)

        await services.builds.submit(build, submitter_id=message.author.id, ai_generated=True)
    except Exception:
        logger.exception("Failed to import message %s from channel %s", message.id, message.channel.id)
        return ImportSummary(failed=1)

    logger.info("Imported message %s as build %s", message.id, build.id)
    return ImportSummary(imported=1)


async def run_import(
    client: discord.Client,
    services: ApplicationServices,
    *,
    channel_ids: Sequence[int],
    model: str,
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

        summaries = await asyncio.gather(*(process_channel(channel, services, model=model) for channel in channels))
        return sum(summaries, start=ImportSummary())
    finally:
        await client.close()


async def main(*, channel_ids: Sequence[int], model: str) -> None:
    """Create process resources and run the historical import."""
    process_config = BotProcessConfig.from_environment()
    intents = discord.Intents.default()
    intents.message_content = True

    async with (
        create_application_runtime(process_config.runtime) as runtime,
        discord.Client(intents=intents) as client,
        asyncio.TaskGroup() as tasks,
    ):
        tasks.create_task(client.start(process_config.token))
        import_task = tasks.create_task(
            run_import(
                client,
                runtime.services,
                channel_ids=channel_ids,
                model=model,
            )
        )

    summary = import_task.result()
    logger.info(
        "Import complete: %d imported, %d existing, %d ignored, %d failed",
        summary.imported,
        summary.existing,
        summary.ignored,
        summary.failed,
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel-id",
        dest="channel_ids",
        action="append",
        type=int,
        help="Discord text-channel ID to import; repeat for multiple channels.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("HISTORICAL_LOG_MODEL", DEFAULT_MODEL),
        help="Text-generation model passed to the configured OpenAI-compatible provider.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    arguments = parse_args()
    asyncio.run(
        main(
            channel_ids=arguments.channel_ids or DEFAULT_CHANNEL_IDS,
            model=arguments.model,
        )
    )
