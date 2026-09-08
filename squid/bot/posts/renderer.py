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
    """Decides where one kind of resource is posted and what it says.

    A renderer answers only "what should be true now"; PostReconciler sends, edits, deletes and records, so a
    renderer need not be idempotent or know whether a post exists.
    """

    resource_kind: ResourceKind
    """The kind this renderer is registered for; the reconciler picks a renderer by it."""

    repost_if_deleted: bool
    """Whether a post someone deleted by hand is posted again.

    A starboard entry is a mirror and returns; a moderator deleting a build card meant to remove it.
    """

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        """Return every post this resource should have.

        None means the resource is gone and every post for it is deleted; an empty sequence means it exists but
        shows nowhere.
        """
        ...

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        """Run once per newly sent post, after it is recorded; never on an edit."""
        ...
