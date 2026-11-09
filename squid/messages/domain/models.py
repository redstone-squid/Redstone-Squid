"""Discord message domain values."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import AliasChoices, Field
from whenever import Instant


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """An immutable snapshot of details observed on a Discord message.

    The message ID identifies one stored record even when several features use
    it, such as a build source and a starboard entry. Channel and author IDs may
    be unknown in drafts; persisting a message requires an author. Saved drafts
    using the older message_id field are accepted when deserializing.
    """

    id: Annotated[int, Field(validation_alias=AliasChoices("id", "message_id"))]
    channel_id: int | None = None
    author_id: int | None = None
    guild_id: int | None = None
    """None in DMs."""
    content: str | None = None
    created_at: Instant | None = None

    @property
    def link(self) -> str | None:
        """Return the message jump link, or None if its channel is unknown.

        DM links are unsupported and raise NotImplementedError.
        """
        if self.channel_id is None:
            return None
        if self.guild_id is None:
            msg = "This message is from DMs."
            raise NotImplementedError(msg)
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.id}"
