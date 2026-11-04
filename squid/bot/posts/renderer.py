"""What a resource wants rendered into Discord, and who decides it."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import discord

import squid_ui_discord as sd
from squid.posts.domain import ResourceKind, Surface


@dataclass(frozen=True, slots=True)
class DesiredPost:
    """One post a resource wants to exist, and what it should currently say."""

    channel_id: int
    guild_id: int
    surface: Surface
    payload: sd.message_payload.MessagePayload
    allowed_mentions: discord.AllowedMentions = field(default_factory=sd.delivery.no_mentions)


class PostRenderer(Protocol):
    """Choose destinations and content for one resource kind.

    `desired` may read existing posts when location or threshold decisions depend
    on them. The reconciler owns sending, editing, deletion, and post registration;
    `after_send` handles additions such as baseline reactions.
    """

    resource_kind: ResourceKind

    repost_if_deleted: bool
    """Whether a previously recorded post may be replaced when it is unavailable.

    Starboard mirrors allow replacement; build and vote cards preserve removal.
    """

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        """Return at most one desired post per channel, or `None` if the resource is gone.

        Both `None` and an empty sequence remove the resource's existing posts.
        """
        ...

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        """React to a post that has just been created, e.g. to add vote reactions."""
        ...
