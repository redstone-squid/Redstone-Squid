from dataclasses import replace
from typing import Any

from whenever import Instant

from squid.reactions.domain import ReactionActor
from squid.starboard.application import PendingVote, StarboardService
from squid.starboard.domain import OriginMessage, StarboardConfig, StarboardEmoji


class FakeRepository:
    def __init__(self, config: StarboardConfig) -> None:
        self.config = config
        self.relevant_calls = 0
        self.recorded: list[PendingVote] = []

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]:
        self.relevant_calls += 1
        return frozenset(item.emoji for item in self.config.emojis)

    async def configs_for_source(self, guild_id: int, channel_id: int) -> tuple[StarboardConfig, ...]:
        return (self.config,)

    async def role_multipliers(self, starboard_id: int) -> dict[int, float]:
        return {50: 2.0}

    async def record_votes(
        self, origin: OriginMessage, user_id: int, votes: tuple[PendingVote, ...] | list[PendingVote]
    ) -> tuple[()]:
        self.recorded.extend(votes)
        return ()


def make_config(**settings: Any) -> StarboardConfig:
    base = StarboardConfig(
        id=1,
        guild_id=10,
        channel_id=20,
        name="main",
        emojis=(StarboardEmoji("⭐", "up", 1.5),),
    )
    return replace(base, **settings)


def make_origin() -> OriginMessage:
    return OriginMessage(100, 10, 30, 40, author_is_bot=False, posted_at=Instant.now().subtract(seconds=60))


async def test_service_caches_relevant_emojis() -> None:
    repository = FakeRepository(make_config())
    service = StarboardService(repository)  # type: ignore[arg-type]

    assert await service.is_relevant_emoji(10, "⭐")
    assert await service.is_relevant_emoji(10, "⭐")
    assert repository.relevant_calls == 1


async def test_service_combines_highest_role_and_emoji_multiplier() -> None:
    repository = FakeRepository(make_config())
    service = StarboardService(repository)  # type: ignore[arg-type]

    result = await service.record_vote(make_origin(), ReactionActor(41, 10, frozenset({50})), "⭐")

    assert not result.remove_reaction
    assert len(repository.recorded) == 1
    assert repository.recorded[0].weight == 3.0


async def test_service_only_requests_invalid_reaction_removal_when_configured() -> None:
    repository = FakeRepository(make_config(require_image=True, remove_invalid_reactions=True))
    service = StarboardService(repository)  # type: ignore[arg-type]

    result = await service.record_vote(make_origin(), ReactionActor(41, 10), "⭐")

    assert result.remove_reaction
    assert repository.recorded == []
