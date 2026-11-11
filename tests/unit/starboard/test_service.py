from dataclasses import replace
from functools import partial
from typing import Any

from whenever import Instant

from squid.messages.models import Message
from squid.reactions.domain import ReactionActor
from squid.starboard.application import PendingVote, StarboardService
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEmoji
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeRepository:
    def __init__(self, config: StarboardConfig) -> None:
        self.config = config
        self.relevant_calls: list[int] = []
        self.recorded: list[PendingVote] = []

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]:
        self.relevant_calls.append(guild_id)
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


class FakeStarboardUnitOfWork(FakeUnitOfWork[FakeRepository]):
    @property
    def starboards(self) -> FakeRepository:
        return self.state


def _copy_repository(repository: FakeRepository) -> FakeRepository:
    copied = FakeRepository(
        replace(repository.config, emojis=tuple(replace(item) for item in repository.config.emojis))
    )
    copied.recorded = [replace(vote, config=replace(vote.config)) for vote in repository.recorded]
    # Keep lookup observations across read scopes, whose state is never committed.
    copied.relevant_calls = repository.relevant_calls
    return copied


def make_config(**settings: Any) -> StarboardConfig:
    base = StarboardConfig(
        id=1,
        guild_id=10,
        channel_id=20,
        name="main",
        emojis=(StarboardEmoji(emoji="⭐", direction="up", multiplier=1.5),),
    )
    return replace(base, **settings)


def make_origin() -> OriginMessage:
    return OriginMessage(
        id=100,
        author_is_bot=False,
        message=Message(
            id=100, guild_id=10, channel_id=30, author_id=40, created_at=Instant.now().subtract(seconds=60)
        ),
    )


async def test_service_reads_current_emojis_across_edits() -> None:
    repository = FakeRepository(make_config())
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]

    assert await service.is_relevant_emoji(10, "⭐")
    assert await service.is_relevant_emoji(10, "⭐")
    assert len(repository.relevant_calls) == 2


async def test_service_combines_highest_role_and_emoji_multiplier() -> None:
    repository = FakeRepository(make_config())
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]

    result = await service.record_vote(make_origin(), ReactionActor(41, 10, frozenset({50})), "⭐")

    assert not result.remove_reaction
    assert len(committed.value.recorded) == 1
    assert committed.value.recorded[0].weight == 3.0


async def test_service_only_requests_invalid_reaction_removal_when_configured() -> None:
    repository = FakeRepository(make_config(require_image=True, remove_invalid_reactions=True))
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]

    result = await service.record_vote(make_origin(), ReactionActor(41, 10), "⭐")

    assert result.remove_reaction
    assert committed.value.recorded == []
