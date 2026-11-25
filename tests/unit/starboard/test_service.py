from dataclasses import replace
from functools import partial
from typing import Any

import pytest
from whenever import Instant

from squid.core.actors import ActingContext, Actor, AnonymousActor, UserActor, WorkerActor
from squid.core.errors import AuthenticationError, AuthorizationError
from squid.core.messages.models import Message
from squid.core.permissions.application.ports import PermissionRepository, PermissionSubject, SubjectRecords
from squid.core.starboard.application import PendingVote, StarboardService
from squid.core.starboard.models import OriginMessage, StarboardConfig, StarboardEmoji
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeRepository:
    def __init__(self, config: StarboardConfig) -> None:
        self.config = config
        self.relevant_calls: list[int] = []
        self.recorded: list[PendingVote] = []
        self.withdrawn: list[int] = []

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> tuple[()]:
        self.withdrawn.append(user_id)
        return ()

    async def recount_votes(self, origin: OriginMessage, votes: tuple[tuple[int, PendingVote], ...]) -> tuple[()]:
        self.recorded = [vote for _, vote in votes]
        return ()

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
    def permissions(self) -> PermissionRepository:
        return self

    async def epoch(self) -> int:
        return 1

    async def load_for_subject(self, subject: PermissionSubject) -> SubjectRecords:
        return SubjectRecords(1, subject)

    @property
    def starboards(self) -> FakeRepository:
        return self.state


def _copy_repository(repository: FakeRepository) -> FakeRepository:
    copied = FakeRepository(
        replace(repository.config, emojis=tuple(replace(item) for item in repository.config.emojis))
    )
    copied.withdrawn = repository.withdrawn.copy()
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

    assert await service.is_relevant_emoji(10, "⭐", context=ActingContext.direct(AnonymousActor()))

    committed.value.config = replace(committed.value.config, emojis=())

    assert not await service.is_relevant_emoji(10, "⭐", context=ActingContext.direct(AnonymousActor()))


async def test_service_combines_highest_role_and_emoji_multiplier() -> None:
    repository = FakeRepository(make_config())
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]

    result = await service.record_vote(
        make_origin(),
        "⭐",
        context=ActingContext.for_actor(UserActor(discord_user_id=41, guild_id=10, discord_role_ids=frozenset({50}))),
    )

    assert not result.remove_reaction
    assert len(committed.value.recorded) == 1
    assert committed.value.recorded[0].weight == 3.0


async def test_service_only_requests_invalid_reaction_removal_when_configured() -> None:
    repository = FakeRepository(make_config(require_image=True, remove_invalid_reactions=True))
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]

    result = await service.record_vote(
        make_origin(), "⭐", context=ActingContext.for_actor(UserActor(discord_user_id=41, guild_id=10))
    )

    assert result.remove_reaction
    assert committed.value.recorded == []


async def test_account_identity_cannot_stand_in_for_a_discord_reaction() -> None:
    repository = FakeRepository(make_config())
    committed = CommittedState(repository, clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]
    actor = UserActor(account_id=7, guild_id=10)
    with pytest.raises(AuthenticationError):
        await service.record_vote(make_origin(), "⭐", context=ActingContext.for_actor(actor))
    with pytest.raises(AuthenticationError):
        await service.recount(
            make_origin(),
            ((actor, "⭐"),),
            context=ActingContext.direct(UserActor(discord_user_id=64, guild_id=10, is_bot_owner=True)),
        )
    assert committed.value.recorded == []


async def test_withdrawal_only_removes_the_invoking_discord_users_vote() -> None:
    committed = CommittedState(FakeRepository(make_config()), clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]
    await service.withdraw_vote(100, "⭐", context=ActingContext.direct(UserActor(discord_user_id=51, guild_id=10)))
    assert committed.value.withdrawn == [51]


@pytest.mark.parametrize(
    "actor",
    [
        UserActor(account_id=1, guild_id=11, is_bot_owner=True),
        WorkerActor(worker_name="other"),
        UserActor(discord_user_id=7, guild_id=10),
        UserActor(account_id=1, guild_id=10, is_bot_owner=True, credential_scopes=frozenset()),
    ],
)
async def test_recount_requires_the_guild_and_current_permission(actor: Actor) -> None:
    committed = CommittedState(FakeRepository(make_config()), clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]
    with pytest.raises(AuthorizationError):
        await service.recount(make_origin(), (), context=ActingContext.direct(actor))


async def test_gateway_cleanup_rejects_human_callers() -> None:
    committed = CommittedState(FakeRepository(make_config()), clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]
    context = ActingContext.direct(UserActor(discord_user_id=51, guild_id=10, is_bot_owner=True))
    with pytest.raises(AuthorizationError):
        await service.clear_votes(100, context=context)
    with pytest.raises(AuthorizationError):
        await service.mark_origin_deleted(100, context=context)


async def test_authorized_recount_records_the_observed_voter() -> None:
    committed = CommittedState(FakeRepository(make_config()), clone=_copy_repository)
    service = StarboardService(uow_factory=partial(FakeStarboardUnitOfWork, committed))  # type: ignore[arg-type]
    voter = UserActor(discord_user_id=51, guild_id=10)
    requester = ActingContext.direct(UserActor(account_id=1, guild_id=10, is_bot_owner=True))
    await service.recount(make_origin(), ((voter, "⭐"),), context=requester)
    assert len(committed.value.recorded) == 1
