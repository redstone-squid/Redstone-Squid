"""Discord message domain values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class MessageFact:
    """What is true about a Discord message, independent of why we care.

    One row per Discord message, shared by every use: a build's provenance, a
    starboard origin, and a vote target are all the same message, recorded once.
    """

    id: int
    channel_id: int
    author_id: int
    guild_id: int | None = None
    """None in DMs."""
    content: str | None = None
    created_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """A stored message fact, as read back out of persistence."""

    id: int
    channel_id: int | None
    author_id: int
    guild_id: int | None = None
    content: str | None = None
    created_at: Instant | None = None
    observed_at: Instant | None = None
    edited_at: Instant | None = None
    deleted_at: Instant | None = None

    @property
    def is_deleted(self) -> bool:
        """Whether Discord has reported this message gone."""
        return self.deleted_at is not None
