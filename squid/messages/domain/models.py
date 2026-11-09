"""Discord message domain values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """An immutable snapshot of details observed on a Discord message.

    The message ID identifies one stored record even when several features use
    it, such as a build source and a starboard entry.
    """

    id: int
    channel_id: int
    author_id: int
    guild_id: int | None = None
    """None in DMs."""
    content: str | None = None
    created_at: Instant | None = None
