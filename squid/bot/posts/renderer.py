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
    """Decides where one kind of resource should be posted, and what it says.

    Renderers answer only "what should be true now". Sending, editing, deleting and
    recording are the reconciler's job, so a renderer never has to be idempotent or
    know whether a post already exists.
    """

    resource_kind: ResourceKind

    repost_if_deleted: bool
    """Whether to post again after someone deletes a post by hand.

    The surfaces genuinely disagree. A starboard entry is a mirror and should return;
    a moderator deleting a build card meant to remove it.
    """

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        """Return every post this resource should have, or None if it is gone.

        None means "delete everything for this resource", which is how a deleted build
        or a closed-and-cleaned-up session removes its cards.
        """
        ...

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        """React to a post that has just been created, e.g. to add vote reactions."""
        ...
