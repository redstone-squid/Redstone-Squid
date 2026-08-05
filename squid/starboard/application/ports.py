"""Starboard application ports and transport plans."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.starboard.domain import (
    EntryAction,
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


@dataclass(frozen=True, slots=True)
class EntryPlan:
    """A transport-neutral materialization operation."""

    config: StarboardConfig
    origin: OriginMessage
    entry: StarboardEntry
    action: EntryAction


class StarboardRepository(Protocol):
    """Persistence operations required by :class:`StarboardService`."""

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]: ...

    async def configs_for_source(self, guild_id: int, channel_id: int) -> Sequence[StarboardConfig]: ...

    async def role_multipliers(self, starboard_id: int) -> Mapping[int, float]: ...

    async def record_votes(
        self, origin: OriginMessage, user_id: int, votes: Sequence[PendingVote]
    ) -> Sequence[EntryPlan]: ...

    async def recount_votes(
        self, origin: OriginMessage, votes: Sequence[tuple[int, PendingVote]]
    ) -> Sequence[EntryPlan]: ...

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> Sequence[EntryPlan]: ...

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> Sequence[EntryPlan]: ...

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> Sequence[EntryPlan]: ...

    async def mark_origin_deleted(self, origin_message_id: int) -> Sequence[EntryPlan]: ...

    async def mark_posted(
        self, starboard_id: int, origin_message_id: int, message_id: int, channel_id: int
    ) -> None: ...

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None: ...

    async def mark_removed(self, starboard_id: int, origin_message_id: int) -> None: ...

    async def reset_deleted_post(self, posted_message_id: int) -> tuple[int, int] | None: ...

    async def disable_channel(self, channel_id: int) -> None: ...

    async def create(self, config: StarboardConfig) -> StarboardConfig: ...

    async def delete(self, guild_id: int, name: str) -> bool: ...

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]: ...

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None: ...

    async def update(self, guild_id: int, name: str, settings: Mapping[str, object]) -> StarboardConfig | None: ...

    async def set_emojis(self, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None: ...

    async def set_role_multiplier(self, starboard_id: int, role_id: int, multiplier: float | None) -> None: ...
