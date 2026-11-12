"""Import historical build-log messages through retained inference and submission drafts.

Dry runs call the model but do not retain inputs or submit drafts. Real imports queue
shared finalization; the worker must be running to finish them. Repeat the same bounds
and grouping options to resume retained runs without changing their bundle identities.
"""

import argparse
import asyncio
import logging
import math
import sys
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anyio
import discord

from squid.accounts.domain import IdentityProvider
from squid.bootstrap import create_bot_runtime
from squid.bot.submission.ingestion import ingest_message_bundle, message_bundle_run_id
from squid.bot.submission.message_context import assemble_bundle
from squid.config import load_bot_process_config
from squid.runtime import BotServices

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportOptions:
    model: str
    reasoning_effort: str | None = None
    concurrency: int = 4
    after: datetime | discord.Object | None = None
    before: datetime | discord.Object | None = None
    limit: int | None = None
    group_window: float = 300
    group_max_messages: int = 8
    include_images: bool = True
    dry_run: bool = False
    channel_consent: bool = False


@dataclass(frozen=True, slots=True)
class ImportSummary:
    groups: int = 0
    processed: int = 0
    inferred_candidates: int = 0
    skipped_existing: int = 0
    skipped_consent: int = 0
    failed: int = 0

    def __add__(self, other: ImportSummary) -> ImportSummary:
        return ImportSummary(
            **{field.name: getattr(self, field.name) + getattr(other, field.name) for field in fields(self)}
        )


@dataclass(frozen=True, slots=True)
class MessageBundle:
    primary: tuple[discord.Message, ...]
    preceding: tuple[discord.Message, ...]


async def iter_bundles(channel: discord.TextChannel, options: ImportOptions) -> AsyncIterator[MessageBundle]:
    """Stream consecutive same-author groups with three preceding messages of context.

    An author change, an outside reply, or the gap/count limit closes a group.
    Bot messages supply context but are never primary inputs. Memory is bounded by
    the group size and lookback, independently of channel history size.
    """
    lookback: deque[discord.Message] = deque(maxlen=3)
    primary: list[discord.Message] = []
    preceding: tuple[discord.Message, ...] = ()
    async for message in channel.history(
        limit=options.limit, oldest_first=True, after=options.after, before=options.before
    ):
        reference_id = message.reference.message_id if message.reference is not None else None
        if primary and (
            message.author.id != primary[0].author.id
            or len(primary) >= options.group_max_messages
            or (message.created_at - primary[-1].created_at).total_seconds() > options.group_window
            or (reference_id is not None and reference_id not in {item.id for item in primary})
        ):
            yield MessageBundle(tuple(primary), preceding)
            primary = []
        if not message.author.bot:
            if not primary:
                preceding = tuple(lookback)
            primary.append(message)
        lookback.append(message)
    if primary:
        yield MessageBundle(tuple(primary), preceding)


async def process_group(bundle: MessageBundle, services: BotServices, options: ImportOptions) -> ImportSummary:
    """Check consent and existing builds before inference; isolate failures per bundle."""
    primary = bundle.primary
    try:
        if not options.channel_consent:
            account = await services.accounts.get_account_by_identity(
                IdentityProvider.DISCORD, str(primary[0].author.id)
            )
            if account is None or account.id is None or account.needs_consent_refresh:
                return ImportSummary(groups=1, skipped_consent=1)
        retained = await services.submission_inference.public_status(message_bundle_run_id(primary))
        # A saved build may be only one candidate of a retained run. Resume its other
        # candidates before consulting the legacy source-message duplicate guard.
        if retained is None:
            existing = [bool(await services.builds.list_ids_for_source_message(message.id)) for message in primary]
            if all(existing):
                return ImportSummary(groups=1, skipped_existing=1)
            if any(existing):
                logger.error(
                    "Partially imported bundle %s; rerun with narrower bounds", [message.id for message in primary]
                )
                return ImportSummary(groups=1, failed=1)
        if options.dry_run:
            inputs = await assemble_bundle(primary, preceding=bundle.preceding, include_images=options.include_images)
            candidates = await services.build_inference.infer(
                inputs, model=options.model, reasoning_effort=options.reasoning_effort
            )
            logger.info("Dry run group %s: %r", [message.id for message in primary], candidates)
            return ImportSummary(groups=1, processed=1, inferred_candidates=len(candidates))
        run_id = await ingest_message_bundle(
            primary,
            bundle.preceding,
            services,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            include_images=options.include_images,
        )
        logger.info("Processed group %s as inference run %s", [message.id for message in primary], run_id)
        return ImportSummary(groups=1, processed=1)
    except Exception:
        logger.exception("Failed to import message group beginning at %s", primary[0].id)
        return ImportSummary(groups=1, failed=1)


async def process_channel(channel: discord.TextChannel, services: BotServices, options: ImportOptions) -> ImportSummary:
    """Process streamed history with a fixed worker pool and bounded queue."""
    summary = ImportSummary()
    send, receive = anyio.create_memory_object_stream[MessageBundle](options.concurrency)

    async def worker() -> None:
        nonlocal summary
        async with receive.clone() as queue:
            async for bundle in queue:
                result = await process_group(bundle, services, options)
                summary += result

    async with send, receive, anyio.create_task_group() as tasks:
        for _ in range(options.concurrency):
            tasks.start_soon(worker)
        async with send:
            async for bundle in iter_bundles(channel, options):
                await send.send(bundle)
    logger.info("Processed channel %s: %s", channel.id, summary)
    return summary


async def run_import(
    client: discord.Client, services: BotServices, channel_ids: Sequence[int], options: ImportOptions
) -> ImportSummary:
    """Import channels sequentially so concurrency is bounded across the whole run."""
    summary = ImportSummary()
    for channel_id in dict.fromkeys(channel_ids):
        channel = await client.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            msg = f"Discord channel {channel_id} is not a text channel."
            raise TypeError(msg)
        summary += await process_channel(channel, services, options)
    return summary


def _history_boundary(value: str) -> datetime | discord.Object:
    if value.isdecimal() and int(value) > 0:
        return discord.Object(id=int(value))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed
    except ValueError:
        pass
    msg = "Use a positive snowflake or ISO-8601 timestamp including timezone"
    raise argparse.ArgumentTypeError(msg)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        msg = "Must be at least one"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _window(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        msg = "Must be a finite, nonnegative number of seconds"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id", dest="channel_ids", action="append", type=_positive_int)
    parser.add_argument("--model", help="Override the configured inference model.")
    parser.add_argument("--reasoning-effort", help="Override the configured reasoning effort.")
    parser.add_argument("--after", type=_history_boundary, help="Exclusive oldest snowflake or timestamp.")
    parser.add_argument("--before", type=_history_boundary, help="Exclusive newest snowflake or timestamp.")
    parser.add_argument("--limit", type=_positive_int, help="Maximum messages read per channel, including bots.")
    parser.add_argument("--concurrency", type=_positive_int, default=4)
    parser.add_argument("--group-window", type=_window, default=300)
    parser.add_argument("--group-max-messages", type=_positive_int, default=8)
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="Call inference without retaining or submitting results."
    )
    parser.add_argument(
        "--channel-consent",
        action="store_true",
        help="Use the selected channels' collection policy instead of per-account consent for this import only.",
    )
    arguments = parser.parse_args(argv)
    if arguments.after is not None and arguments.before is not None:
        after = arguments.after.created_at if isinstance(arguments.after, discord.Object) else arguments.after
        before = arguments.before.created_at if isinstance(arguments.before, discord.Object) else arguments.before
        if after >= before:
            parser.error("--after must precede --before")
    return arguments


async def main(arguments: argparse.Namespace) -> int:
    process_config = load_bot_process_config()
    options = ImportOptions(
        model=arguments.model or process_config.openai.chat_model,
        reasoning_effort=arguments.reasoning_effort or process_config.openai.reasoning_effort,
        concurrency=arguments.concurrency,
        after=arguments.after,
        before=arguments.before,
        limit=arguments.limit,
        group_window=arguments.group_window,
        group_max_messages=arguments.group_max_messages,
        include_images=not arguments.no_images,
        dry_run=arguments.dry_run,
        channel_consent=arguments.channel_consent,
    )
    async with (
        create_bot_runtime(process_config.runtime) as runtime,
        discord.Client(intents=discord.Intents.none()) as client,
    ):
        channel_ids = arguments.channel_ids
        if not channel_ids:
            channel_ids = (await runtime.services.settings.get_runtime()).build_log_channel_ids
        if not channel_ids:
            logger.error("No build-log channels configured; provide --channel-id")
            return 1
        if options.channel_consent:
            logger.info("Using channel-policy consent for this import only: %s", channel_ids)
        # HTTP history only: no gateway listener or background bot activity is needed.
        await client.login(process_config.discord.token.get_secret_value())
        summary = await run_import(client, runtime.services, channel_ids, options)
    logger.info("Import complete: %s", summary)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(main(parse_args())))
