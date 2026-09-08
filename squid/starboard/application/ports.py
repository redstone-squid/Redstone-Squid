"""Starboard application ports and transport plans."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.starboard.domain import (
    OriginMessage,
    StarboardConfig,
    StarboardDirection,
    StarboardEmoji,
    StarboardEntry,
)


@dataclass(frozen=True, slots=True)
class PendingVote:
    """A policy-approved vote ready for atomic persistence."""

    config: StarboardConfig
    emoji: str
    direction: StarboardDirection
    weight: float


type EntryKey = tuple[int, int]
"""One entry, as (starboard id, origin message id)."""


@dataclass(frozen=True, slots=True)
class EntryState:
    """Everything needed to decide and render one entry.

    Carries no action: whether a post should exist follows from the score and the posts that are actually there.
    """

    config: StarboardConfig
    origin: OriginMessage
    entry: StarboardEntry


class StarboardRepository(Protocol):
    """Persistence operations required by `StarboardService`.

    Every method returning `Sequence[EntryKey]` rescores the affected entries and returns only those whose score
    moved away from the last rendered one, so the caller can re-render exactly those.
    """

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]:
        """Every emoji configured on an enabled starboard that sources this guild."""
        ...

    async def configs_for_source(self, guild_id: int, channel_id: int) -> Sequence[StarboardConfig]:
        """Enabled starboards sourcing this channel, including those granted the whole guild."""
        ...

    async def role_multipliers(self, starboard_id: int) -> Mapping[int, float]:
        """Vote weight per role id; roles absent from the mapping weigh 1."""
        ...

    async def record_votes(
        self, origin: OriginMessage, user_id: int, votes: Sequence[PendingVote]
    ) -> Sequence[EntryKey]:
        """Upsert the origin and this user's one vote per board, then rescore.

        A user holds at most one vote per starboard on a given message, so a second emoji replaces the first.
        """
        ...

    async def recount_votes(
        self, origin: OriginMessage, votes: Sequence[tuple[int, PendingVote]]
    ) -> Sequence[EntryKey]:
        """Replace every vote on the origin with `(user_id, vote)` pairs read from Discord, then rescore.

        Returns every touched entry whether or not its score moved, since a recount also repairs stale renders.
        """
        ...

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> Sequence[EntryKey]:
        """Drop one user's vote on that emoji and rescore the boards it counted towards."""
        ...

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> Sequence[EntryKey]:
        """Drop every vote on the origin, or only those cast with `emoji`, and rescore."""
        ...

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> Sequence[EntryKey]:
        """Rescore every entry for the origin; `force` returns them all rather than only the changed ones."""
        ...

    async def mark_origin_deleted(self, origin_message_id: int) -> Sequence[EntryKey]:
        """Record the source message as deleted, returning every entry to re-render, or nothing if it is unknown."""
        ...

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState | None:
        """Load one entry with its board config and origin facts, or `None` if any of the three is missing."""
        ...

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        """Record the score a post now shows, so an unchanged entry stops being returned as changed."""
        ...

    async def disable_channel(self, channel_id: int) -> None:
        """Disable every starboard that posts into this channel, after the channel becomes unusable."""
        ...

    async def create(self, config: StarboardConfig) -> StarboardConfig:
        """Insert the board with a guild-wide source grant, returning it with its assigned id."""
        ...

    async def delete(self, guild_id: int, name: str) -> bool:
        """Delete the board by case-insensitive name; `False` if the guild has no such board."""
        ...

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]:
        """Every board owned by the guild, by name."""
        ...

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        """The board with this case-insensitive name, or `None`."""
        ...

    async def update(self, guild_id: int, name: str, settings: Mapping[str, object]) -> StarboardConfig | None:
        """Apply column updates, returning the reloaded board or `None` if the guild has no such board.

        Raises `ValueError` when `settings` is empty or names anything other than a `StarboardConfig` column.
        """
        ...

    async def set_emojis(self, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None:
        """Replace the board's emoji set, repositioning them in the order given."""
        ...

    async def set_role_multiplier(self, starboard_id: int, role_id: int, multiplier: float | None) -> None:
        """Set the role's vote weight, or remove it when `multiplier` is `None`."""
        ...
