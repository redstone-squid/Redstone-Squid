import asyncio
from collections.abc import AsyncGenerator, Mapping
from typing import cast

import pytest
from sqlalchemy import Table, insert, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.messages.infrastructure.models import Message
from squid.persistence.base import Base
from squid.settings.infrastructure.models import ServerSetting
from squid.voting.domain import DEFAULT_VOTE_OPTIONS, StoredVoteMutation, VoteChoice, VoteOption, VoteTarget
from squid.voting.infrastructure.models import (
    BuildVoteSession,
    DeleteLogVoteSession,
    GenericVoteSession,
    Vote,
    VoteSession,
    VoteSessionOption,
)
from squid.voting.infrastructure.repository import VoteRepository
from tests.helpers.schema import with_foreign_key_targets

GUILD_ID = 503
CHANNEL_ID = 502
TARGET_MESSAGE_ID = 501
AUTHOR_ACCOUNT_IDS = (99, 100)
VOTER_ACCOUNT_IDS = (7, 8)

_TABLES: tuple[Table, ...] = with_foreign_key_targets(
    cast(Table, VoteSession.__table__),
    cast(Table, VoteSessionOption.__table__),
    cast(Table, BuildVoteSession.__table__),
    cast(Table, DeleteLogVoteSession.__table__),
    cast(Table, GenericVoteSession.__table__),
    cast(Table, Vote.__table__),
    cast(Table, Message.__table__),
)


@pytest.fixture(autouse=True)
async def vote_schema(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create the voting tables from the models rather than from a hand-written copy.

    The DDL this replaces had to be edited by hand every time a voting column changed,
    and any column it forgot was one the repository was never tested against. Building
    from the real metadata means the constraints under test are the shipped ones.
    """
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))


@pytest.fixture(autouse=True)
async def referenced_rows(
    vote_schema: None, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Satisfy the account and guild foreign keys the real schema declares.

    Written through the mapped tables so the ids stay the fixed literals the assertions
    read, which `Account`'s generated primary key does not allow through the ORM.
    """
    async with async_session_factory.begin() as session:
        await session.execute(
            insert(Account).values([{"id": account_id} for account_id in (*AUTHOR_ACCOUNT_IDS, *VOTER_ACCOUNT_IDS)])
        )
        await session.execute(insert(ServerSetting).values(server_id=GUILD_ID))


async def attach_vote_message(
    session: AsyncSession, *, message_id: int, vote_session_id: int, content: str | None = None
) -> None:
    """Register the Discord message a vote session is rendered on."""
    await session.execute(
        insert(Message).values(
            id=message_id,
            server_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            author_id=AUTHOR_ACCOUNT_IDS[0],
            purpose="vote",
            content=content,
            vote_session_id=vote_session_id,
        )
    )


async def seed_delete_log_vote(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    pass_threshold: int = 3,
    fail_threshold: int = -3,
    votes: Mapping[int, float] | None = None,
) -> tuple[int, int]:
    """Persist an open delete-log vote session and return its session/message IDs.

    Written against the mapped tables rather than the repository under test: these rows
    stand in for sessions an earlier release created, so building them through the code
    being tested would make several of these tests tautological.
    """
    async with session_factory.begin() as session:
        vote_session_id = (
            await session.execute(
                insert(VoteSession)
                .values(
                    status="open",
                    result="pending",
                    author_account_id=AUTHOR_ACCOUNT_IDS[0],
                    kind="delete_log",
                    pass_threshold=pass_threshold,
                    fail_threshold=fail_threshold,
                )
                .returning(VoteSession.id)
            )
        ).scalar_one()
        message_id = 1_000 + vote_session_id
        # Projected from the domain defaults rather than restated, so that the
        # `snapshot.options == DEFAULT_VOTE_OPTIONS` assertions below prove a round trip
        # instead of comparing two hand-written copies of the same four options.
        await session.execute(
            insert(VoteSessionOption),
            [
                {
                    "vote_session_id": vote_session_id,
                    "identifier": option.identifier,
                    "guild_id": option.guild_id or 0,
                    "emoji": option.emoji,
                    "choice": option.choice.value,
                    "multiplier": option.multiplier,
                    "position": option.position,
                }
                for option in DEFAULT_VOTE_OPTIONS
            ],
        )
        await session.execute(
            insert(DeleteLogVoteSession).values(
                vote_session_id=vote_session_id,
                target_message_id=TARGET_MESSAGE_ID,
                target_channel_id=CHANNEL_ID,
                target_server_id=GUILD_ID,
            )
        )
        await attach_vote_message(
            session, message_id=message_id, vote_session_id=vote_session_id, content="Vote now"
        )
        if votes:
            await session.execute(
                insert(Vote),
                [
                    {
                        "vote_session_id": vote_session_id,
                        "account_id": account_id,
                        "discord_id": account_id * 10,
                        "guild_id": GUILD_ID,
                        "option_id": "deny" if weight < 0 else "approve",
                        "emoji": "👎" if weight < 0 else "👍",
                        "weight": abs(weight),
                    }
                    for account_id, weight in votes.items()
                ],
            )
    return vote_session_id, message_id


async def seed_generic_poll(
    session_factory: async_sessionmaker[AsyncSession],
    repository: VoteRepository,
    *,
    question: str,
    visibility: str,
    deadline: Instant,
    options: tuple[VoteOption, ...],
) -> tuple[int, int]:
    """Open a generic poll through the repository and put a message on it.

    Every value a test asserts on stays in the caller's hands; only the message row,
    which no assertion reads, lives here.
    """
    session_id = await repository.create_generic_session(
        author_account_id=AUTHOR_ACCOUNT_IDS[0],
        guild_id=GUILD_ID,
        question=question,
        visibility=visibility,
        deadline=deadline,
        options=options,
    )
    message_id = 10_000 + session_id
    async with session_factory.begin() as session:
        await attach_vote_message(session, message_id=message_id, vote_session_id=session_id)
    return session_id, message_id


async def test_vote_aggregates_are_persisted_by_repository(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)
    build_session_id = await repository.create_build_session(
        author_account_id=99,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=[("submission_status", "pending", "confirmed")],
    )
    delete_session_id = await repository.create_delete_log_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        message_id=501,
        channel_id=502,
        server_id=503,
    )

    async with async_session_factory() as session:
        build_row = (
            (
                await session.execute(
                    text("SELECT build_id, changes FROM build_vote_sessions WHERE vote_session_id = :id"),
                    {"id": build_session_id},
                )
            )
            .tuples()
            .one()
        )
        delete_row = (
            (
                await session.execute(
                    text(
                        """
                    SELECT target_message_id, target_channel_id, target_server_id
                    FROM delete_log_vote_sessions
                    WHERE vote_session_id = :id
                    """
                    ),
                    {"id": delete_session_id},
                )
            )
            .tuples()
            .one()
        )
        roots = (
            (
                await session.execute(
                    text(
                        """
                        SELECT author_account_id, kind, pass_threshold, fail_threshold, status, result
                        FROM vote_sessions
                        ORDER BY id
                        """
                    )
                )
            )
            .tuples()
            .all()
        )

    assert build_row == (42, [["submission_status", "pending", "confirmed"]])
    assert delete_row == (501, 502, 503)
    assert roots == [
        (99, "build", 3, -3, "open", "pending"),
        (100, "delete_log", 4, -2, "open", "pending"),
    ]


async def test_initial_build_vote_creation_is_idempotent(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)
    first = await repository.get_or_create_build_submission_session(
        author_account_id=99,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=[("submission_status", "pending", "confirmed")],
    )
    second = await repository.get_or_create_build_submission_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        build_id=42,
        changes=[("submission_status", "pending", "confirmed")],
    )

    async with async_session_factory() as session:
        roots = await session.scalar(text("SELECT count(*) FROM vote_sessions"))
        targets = await session.scalar(text("SELECT count(*) FROM build_vote_sessions WHERE build_id = 42"))

    assert second == first
    assert roots == 1
    assert targets == 1


async def test_target_failure_rolls_back_vote_root(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)

    with pytest.raises(StatementError):
        await repository.create_build_session(
            author_account_id=99,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[("invalid", object(), object())],
        )

    async with async_session_factory() as session:
        count = (await session.execute(text("SELECT count(*) FROM vote_sessions"))).scalar_one()
    assert count == 0


async def test_get_by_message_maps_votes_messages_and_delete_target(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vote_session_id, message_id = await seed_delete_log_vote(
        async_session_factory,
        votes={7: 1.0, 8: -1.0},
    )
    repository = VoteRepository(async_session_factory)

    snapshot = await repository.get_by_message(message_id)

    assert snapshot is not None
    assert snapshot.id == vote_session_id
    assert snapshot.votes == {7: 1.0, 8: -1.0}
    assert snapshot.message_ids == (message_id,)
    assert snapshot.options == DEFAULT_VOTE_OPTIONS
    assert snapshot.target == VoteTarget(message_id=501, channel_id=502, server_id=503)


async def test_custom_vote_options_are_persisted_in_order(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)
    options = (
        VoteOption("<:strong_yes:123>", VoteChoice.APPROVE, 2.0),
        VoteOption("👎", VoteChoice.DENY),
    )

    vote_session_id = await repository.create_delete_log_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        message_id=501,
        channel_id=502,
        server_id=503,
        options=options,
    )

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        """
                        SELECT emoji, choice, multiplier
                        FROM vote_session_options
                        WHERE vote_session_id = :vote_session_id
                        ORDER BY position
                        """
                    ),
                    {"vote_session_id": vote_session_id},
                )
            )
            .tuples()
            .all()
        )

    assert rows == [("<:strong_yes:123>", "approve", 2.0), ("👎", "deny", 1.0)]


async def test_cast_vote_replaces_then_toggles_the_same_choice(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, message_id = await seed_delete_log_vote(
        async_session_factory,
        pass_threshold=10,
        fail_threshold=-10,
    )
    repository = VoteRepository(async_session_factory)

    inserted = await repository.cast_vote(message_id, 7, 70, GUILD_ID, "approve", "👍", 1.0)
    replaced = await repository.cast_vote(message_id, 7, 70, GUILD_ID, "deny", "👎", 1.0)
    removed = await repository.cast_vote(message_id, 7, 70, GUILD_ID, "deny", "👎", 1.0)

    assert inserted is not None
    assert (inserted.previous_weight, inserted.current_weight) == (None, 1.0)
    assert replaced is not None
    assert (replaced.previous_weight, replaced.current_weight) == (1.0, 1.0)
    assert removed is not None
    assert (removed.previous_weight, removed.current_weight) == (1.0, None)
    assert removed.session.votes == {}


@pytest.mark.parametrize(
    ("desired_weight", "expected_result"),
    [(1.0, "approved"), (-1.0, "denied")],
)
async def test_cast_vote_closes_at_either_threshold(
    async_session_factory: async_sessionmaker[AsyncSession],
    desired_weight: float,
    expected_result: str,
) -> None:
    _, message_id = await seed_delete_log_vote(
        async_session_factory,
        pass_threshold=1,
        fail_threshold=-1,
    )
    repository = VoteRepository(async_session_factory)

    option_id, emoji = ("approve", "👍") if desired_weight > 0 else ("deny", "👎")
    mutation = await repository.cast_vote(message_id, 7, 70, GUILD_ID, option_id, emoji, abs(desired_weight))

    assert mutation is not None
    assert mutation.just_closed
    assert mutation.session.status == "closed"
    assert mutation.session.result == expected_result


async def test_concurrent_votes_report_exactly_one_closure(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, message_id = await seed_delete_log_vote(
        async_session_factory,
        pass_threshold=1,
        fail_threshold=-10,
    )
    repository = VoteRepository(async_session_factory)

    results = await asyncio.gather(
        repository.cast_vote(message_id, 7, 70, GUILD_ID, "approve", "👍", 1.0),
        repository.cast_vote(message_id, 8, 80, GUILD_ID, "approve", "👍", 1.0),
    )

    mutations = [result for result in results if isinstance(result, StoredVoteMutation)]
    assert len(mutations) == 1
    assert mutations[0].just_closed
    assert sum(result is None for result in results) == 1

    snapshot = await repository.get_by_message(message_id)
    assert snapshot is not None
    assert snapshot.status == "closed"
    assert len(snapshot.votes) == 1


async def test_generic_poll_persists_choices_tallies_and_due_closure(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)
    session_id, message_id = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility="anonymous_hidden",
        # Already past, so the poll is due the moment it opens.
        deadline=Instant.now().subtract(seconds=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=GUILD_ID, label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=GUILD_ID, label="Two"),
        ),
    )

    await repository.cast_vote(message_id, 7, 70, GUILD_ID, "one", "1️⃣", 3)
    await repository.cast_vote(message_id, 8, 80, GUILD_ID, "two", "2️⃣", 1)
    snapshot = await repository.get_by_message(message_id)

    assert snapshot is not None
    assert snapshot.poll is not None
    assert snapshot.poll.question == "Choose one"
    assert snapshot.raw_tallies() == {"one": 1, "two": 1}
    assert snapshot.weighted_tallies() == {"one": 3, "two": 1}
    assert [item.id for item in await repository.list_due(Instant.now())] == [session_id]

    closed = await repository.close_by_id(session_id)
    assert closed is not None
    assert closed.just_closed
    assert closed.session.status == "closed"
    assert await repository.list_due(Instant.now()) == []
