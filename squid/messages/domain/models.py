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
