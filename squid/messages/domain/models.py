"""Discord message domain values."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import AliasChoices, Field
from whenever import Instant


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Identifies a Discord message and where it was posted, without its contents.

    Builds use this to point at their source messages. The stored `Message` row
    is the authority for content and edit or deletion state.
    """

    id: Annotated[int, Field(validation_alias=AliasChoices("id", "message_id"))]
    """The Discord snowflake. Drafts saved before the rename used message_id, which is still accepted."""
    channel_id: int | None = None
    """None when a draft does not know the channel."""
    guild_id: int | None = None
    """None for a DM, or when a draft does not know the guild."""

    @property
    def link(self) -> str | None:
        """Return the message jump link, or None if its channel is unknown.

        A known channel without a guild is treated as a DM. DM links are
        unsupported and raise NotImplementedError.
        """
        if self.channel_id is None:
            return None
        if self.guild_id is None:
            msg = "This message is from DMs."
            raise NotImplementedError(msg)
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.id}"


@dataclass(frozen=True, slots=True)
class MessageSnapshot(MessageRef):
    """Message details copied from Discord at one moment.

    The bot creates snapshots from `discord.Message` objects and passes them to
    `MessageService.observe`, which records them. Submission drafts also keep
    snapshots in their JSON so the messages can be recorded when the draft
    becomes a build.

    A snapshot does not change after it is captured, so it can be older than the
    stored row. Drafts may lack any field except the ID, but recording a message
    requires `author_id`.
    """

    author_id: int | None = None
    content: str | None = None
    created_at: Instant | None = None
