"""Assemble contextual, multimodal message bundles for build inference."""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeVar, cast

import discord

from squid.bot.submission.attachments import classify_attachment
from squid.bot.utils.web import extract_first_frame
from squid.builds.application import BuildInferenceInput, ContextMessage, InlineImage
from squid.core.errors import SquidError

logger = logging.getLogger(__name__)


class GroupableMessage(Protocol):
    """Minimum message shape consumed by the pure grouping function."""

    @property
    def id(self) -> int: ...

    @property
    def author_id(self) -> int: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def reference_id(self) -> int | None: ...


MessageT = TypeVar("MessageT", bound=GroupableMessage)


@dataclass(frozen=True, slots=True)
class MessageGroup[MessageT: GroupableMessage]:
    """Primary author run and interleaved messages retained as context."""

    primary: tuple[MessageT, ...]
    context: tuple[MessageT, ...] = ()


def group_messages(
    messages: Iterable[MessageT], *, window_seconds: float = 300, max_messages: int = 8
) -> list[MessageGroup[MessageT]]:
    """Group chronological messages into author runs without losing interleaved context."""
    if max_messages < 1:
        msg = "max_messages must be at least one"
        raise ValueError(msg)

    groups: list[MessageGroup[MessageT]] = []
    primary: list[MessageT] = []
    interleaved: list[MessageT] = []

    def flush() -> None:
        nonlocal primary, interleaved
        if primary:
            groups.append(MessageGroup(tuple(primary), tuple(interleaved)))
        primary = []
        interleaved = []

    for message in messages:
        if not primary:
            primary.append(message)
            continue
        same_author = message.author_id == primary[0].author_id
        gap = (message.created_at - primary[-1].created_at).total_seconds()
        replies_outside_group = message.reference_id is not None and message.reference_id not in {
            item.id for item in primary
        }
        if same_author and gap <= window_seconds and len(primary) < max_messages and not replies_outside_group:
            primary.append(message)
        elif not same_author:
            interleaved.append(message)
        else:
            flush()
            primary.append(message)
    flush()
    return groups


async def resolve_reply_chain(
    message: discord.Message,
    *,
    max_depth: int = 4,
    cache: dict[int, discord.Message | None],
) -> tuple[discord.Message, ...]:
    """Resolve a message's reply ancestry with caching and cycle protection."""
    parents: list[discord.Message] = []
    current = message
    seen = {message.id}
    for _ in range(max_depth):
        reference = current.reference
        if reference is None or reference.message_id is None or reference.message_id in seen:
            break
        parent_id = reference.message_id
        seen.add(parent_id)
        resolved = reference.resolved
        if isinstance(resolved, discord.DeletedReferencedMessage):
            cache[parent_id] = None
            break
        if isinstance(resolved, discord.Message):
            parent: discord.Message | None = resolved
        elif parent_id in cache:
            parent = cache[parent_id]
        else:
            try:
                parent = await current.channel.fetch_message(parent_id)
            except discord.NotFound, discord.Forbidden, discord.HTTPException:
                parent = None
            cache[parent_id] = parent
        if parent is None:
            break
        parents.append(parent)
        current = parent
    return tuple(parents)


def collect_lookback(history: Sequence[MessageT], group: Sequence[MessageT], *, limit: int = 3) -> tuple[MessageT, ...]:
    """Return the messages immediately preceding a group from chronological history."""
    if not group or limit <= 0:
        return ()
    first_id = group[0].id
    index = next((position for position, item in enumerate(history) if item.id == first_id), 0)
    return tuple(history[max(0, index - limit) : index])


async def collect_images(
    messages: Sequence[discord.Message], *, max_images: int = 6, max_bytes: int = 4 * 1024 * 1024
) -> tuple[InlineImage, ...]:
    """Read oldest-first images and video preview frames within aggregate caps."""
    images: list[InlineImage] = []
    total_bytes = 0
    for message in messages:
        for attachment in message.attachments:
            if len(images) >= max_images:
                return tuple(images)
            try:
                classified = classify_attachment(
                    attachment.filename,
                    attachment.content_type,
                    attachment.size,
                    max_bytes=max_bytes,
                )
            except SquidError:
                continue
            if classified.kind == "schematic":
                continue
            try:
                if classified.kind == "image":
                    data = await attachment.read()
                    if len(data) > max_bytes:
                        continue
                    content_type = classified.content_type
                    origin: Literal["attachment", "video_frame"] = "attachment"
                else:
                    video_data = await attachment.read()
                    if len(video_data) > max_bytes:
                        continue
                    data = (await extract_first_frame(video_data)).getvalue()
                    content_type = "image/png"
                    origin = "video_frame"
            except discord.HTTPException, OSError, RuntimeError:
                logger.warning("Could not read inference image %s", attachment.filename, exc_info=True)
                continue
            if total_bytes + len(data) > max_bytes:
                continue
            images.append(InlineImage(data, content_type, message.id, origin))
            total_bytes += len(data)
    return tuple(images)


def _attachment_summary(message: discord.Message) -> str:
    counts = {"image": 0, "video": 0, "schematic": 0}
    filenames: list[str] = []
    for attachment in message.attachments:
        try:
            classified = classify_attachment(
                attachment.filename,
                attachment.content_type,
                attachment.size,
                max_bytes=max(attachment.size, 1),
            )
        except SquidError:
            continue
        counts[classified.kind] += 1
        if classified.kind == "schematic":
            filenames.append(classified.filename)
    parts = [f"{count} {kind}{'' if count == 1 else 's'}" for kind, count in counts.items() if count]
    parts.extend(filenames)
    return ", ".join(parts)


def _normalize(message: discord.Message, kind: Literal["primary", "reply_parent", "preceding"]) -> ContextMessage:
    return ContextMessage(
        message_id=message.id,
        author_name=message.author.display_name,
        author_id=message.author.id,
        content=message.clean_content,
        timestamp=message.created_at.isoformat(),
        kind=kind,
        attachment_summary=_attachment_summary(message),
    )


async def assemble_bundle(
    primary: Sequence[discord.Message],
    *,
    preceding: Sequence[discord.Message] = (),
    reply_cache: dict[int, discord.Message | None] | None = None,
    include_images: bool = True,
) -> BuildInferenceInput:
    """Resolve reply context and images into one inference input holding no Discord objects."""
    if not primary:
        msg = "A bundle requires at least one primary message"
        raise ValueError(msg)
    cache = reply_cache if reply_cache is not None else {}
    reply_parents: list[discord.Message] = []
    for message in primary:
        for parent in await resolve_reply_chain(message, cache=cache):
            if parent.id not in {item.id for item in primary} and parent.id not in {item.id for item in reply_parents}:
                reply_parents.append(parent)

    context = tuple(_normalize(message, "reply_parent") for message in reply_parents)
    known_ids = {message.message_id for message in context}
    context += tuple(_normalize(message, "preceding") for message in preceding if message.id not in known_ids)
    all_messages = tuple(primary) + tuple(reply_parents) + tuple(preceding)
    images = await collect_images(cast(Sequence[discord.Message], all_messages)) if include_images else ()
    first = primary[0]
    return BuildInferenceInput(
        primary=tuple(_normalize(message, "primary") for message in primary),
        context=context,
        images=images,
        channel_id=first.channel.id,
        server_id=first.guild.id if first.guild is not None else None,
    )
