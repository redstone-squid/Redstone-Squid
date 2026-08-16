import asyncio
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from sqlalchemy import Table, insert, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.builds.domain import Status
from squid.builds.infrastructure.models import Build
from squid.messages.infrastructure.models import Message
from squid.persistence.base import Base
from squid.posts.infrastructure.models import DiscordPost
from squid.settings.infrastructure.models import ServerSetting
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    DeleteLogVoteTarget,
    StoredVoteMutation,
    VoteChoice,
    VoteKind,
    VoteOption,
    VoteSessionResult,
    VoteStatus,
    VoteVisibility,
)
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
from tests.helpers.voting import (
    AUTHOR_ACCOUNT_IDS,
    BUILD_ID,
    CHANNEL_ID,
    GUILD_ID,
    TARGET_MESSAGE_ID,
    VOTER_ACCOUNT_IDS,
    attach_vote_message,
    seed_delete_log_vote,
)

_TABLES: tuple[Table, ...] = with_foreign_key_targets(
    cast(Table, VoteSession.__table__),
    cast(Table, VoteSessionOption.__table__),
    cast(Table, BuildVoteSession.__table__),
    cast(Table, DeleteLogVoteSession.__table__),
    cast(Table, GenericVoteSession.__table__),
    cast(Table, Vote.__table__),
    cast(Table, Message.__table__),
    cast(Table, DiscordPost.__table__),
)


@pytest.fixture(autouse=True)
async def vote_schema(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create the voting tables from the models rather than from a hand-written copy.

    The DDL this replaces had to be edited by hand every time a voting column changed,
    and any column it forgot was one the repository was never tested against. Building
    from the real metadata means the constraints under test are the shipped ones.
    """
    async with async_engine.begin() as connection:
        # `builds` is in the closure through build_vote_sessions.build_id, and it carries a
        # pgvector column, so the type has to exist before the tables are created.
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))


@pytest.fixture(autouse=True)
async def referenced_rows(vote_schema: None, async_session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Satisfy the account, guild and build foreign keys the real schema declares.

    The hand-written schema this replaces declared none of them, so a vote session could
    reference a build or an account that did not exist. Written through the mapped tables
    so the ids stay the fixed literals the assertions read, which the generated primary
    keys do not allow through the ORM.
    """
    async with async_session_factory.begin() as session:
        await session.execute(
            insert(Account).values([{"id": account_id} for account_id in (*AUTHOR_ACCOUNT_IDS, *VOTER_ACCOUNT_IDS)])
        )
        await session.execute(insert(ServerSetting).values(server_id=GUILD_ID))
        # `Build.__table__`, not `Build`: the entity is the base of a joined-inheritance
        # hierarchy, so an ORM-level insert targets the polymorphic join rather than a table.
        await session.execute(
            insert(cast(Table, Build.__table__)).values(
                id=BUILD_ID,
                submission_status=Status.PENDING,
                submitter_account_id=AUTHOR_ACCOUNT_IDS[0],
            )
        )


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> VoteRepository:
    return VoteRepository(async_session_factory)


async def seed_generic_poll(
    session_factory: async_sessionmaker[AsyncSession],
    repository: VoteRepository,
    *,
    question: str,
    visibility: VoteVisibility,
    deadline: Instant,
    options: tuple[VoteOption, ...],
    guild_id: int | None = GUILD_ID,
    attach: bool = True,
) -> tuple[int, int | None]:
    """Open a generic poll through the repository and optionally put a message on it.

    Every value a test asserts on stays in the caller's hands; only the message row,
    which no assertion reads, lives here.
    """
    session_id = await repository.create_generic_session(
        author_account_id=AUTHOR_ACCOUNT_IDS[0],
        question=question,
        visibility=visibility,
        deadline=deadline,
        options=options,
        guild_id=guild_id,
    )
    if not attach:
        return session_id, None
    message_id = 10_000 + session_id
    async with session_factory.begin() as session:
        await attach_vote_message(session, message_id=message_id, vote_session_id=session_id)
    return session_id, message_id


async def test_vote_aggregates_are_persisted_by_repository(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_session_id = await repository.create_build_session(
        author_account_id=99,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=BUILD_ID,
        changes=[("submission_status", "pending", "confirmed")],
    )
    delete_session_id = await repository.create_delete_log_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        message_id=TARGET_MESSAGE_ID,
        channel_id=CHANNEL_ID,
        server_id=GUILD_ID,
    )

    build = await repository.get_by_id(build_session_id)
    delete = await repository.get_by_id(delete_session_id)

    assert build is not None
    assert delete is not None
    assert (build.author_account_id, build.kind, build.pass_threshold, build.fail_threshold) == (
        99,
        VoteKind.BUILD,
        3,
        -3,
    )
    assert (build.status, build.result) == (VoteStatus.OPEN, VoteSessionResult.PENDING)
    assert delete.target == DeleteLogVoteTarget(TARGET_MESSAGE_ID, CHANNEL_ID, GUILD_ID)
    assert (delete.pass_threshold, delete.fail_threshold) == (4, -2)


async def test_initial_build_vote_creation_is_idempotent(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await repository.get_or_create_build_submission_session(
        author_account_id=99,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=BUILD_ID,
        changes=[("submission_status", "pending", "confirmed")],
    )
    second = await repository.get_or_create_build_submission_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        build_id=BUILD_ID,
        changes=[("submission_status", "pending", "confirmed")],
    )

    async with async_session_factory() as session:
        roots = await session.scalar(text("SELECT count(*) FROM vote_sessions"))

    assert second == first
    assert roots == 1


async def test_target_failure_rolls_back_vote_root(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The build exists, so the only thing that can fail here is the unserializable payload.

    Worth stating because a missing build would also raise `StatementError`, and the test
    would then pass without ever reaching the two-insert sequence it is about.
    """
    with pytest.raises(StatementError):
        await repository.create_build_session(
            author_account_id=99,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=BUILD_ID,
            changes=[("invalid", object(), object())],
        )

    async with async_session_factory() as session:
        count = (await session.execute(text("SELECT count(*) FROM vote_sessions"))).scalar_one()
    assert count == 0


async def test_get_by_message_maps_votes_messages_and_delete_target(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vote_session_id, message_id = await seed_delete_log_vote(async_session_factory, votes={7: 1.0, 8: -1.0})

    snapshot = await repository.get_by_message(message_id)

    assert snapshot is not None
    assert snapshot.id == vote_session_id
    assert snapshot.votes == {7: 1.0, 8: -1.0}
    assert snapshot.message_ids == (message_id,)
    assert snapshot.options == DEFAULT_VOTE_OPTIONS
    assert snapshot.target == DeleteLogVoteTarget(TARGET_MESSAGE_ID, CHANNEL_ID, GUILD_ID)


async def test_custom_vote_options_are_persisted_in_order(repository: VoteRepository) -> None:
    options = (
        VoteOption("<:strong_yes:123>", VoteChoice.APPROVE, 2.0),
        VoteOption("👎", VoteChoice.DENY),
    )

    vote_session_id = await repository.create_delete_log_session(
        author_account_id=100,
        pass_threshold=4,
        fail_threshold=-2,
        message_id=TARGET_MESSAGE_ID,
        channel_id=CHANNEL_ID,
        server_id=GUILD_ID,
        options=options,
    )

    snapshot = await repository.get_by_id(vote_session_id)

    assert snapshot is not None
    assert [(option.emoji, option.choice, option.multiplier) for option in snapshot.options] == [
        ("<:strong_yes:123>", VoteChoice.APPROVE, 2.0),
        ("👎", VoteChoice.DENY, 1.0),
    ]


async def test_cast_vote_replaces_then_toggles_the_same_choice(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, message_id = await seed_delete_log_vote(async_session_factory, pass_threshold=10, fail_threshold=-10)

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
    [(1.0, VoteSessionResult.APPROVED), (-1.0, VoteSessionResult.DENIED)],
)
async def test_cast_vote_closes_at_either_threshold(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
    desired_weight: float,
    expected_result: VoteSessionResult,
) -> None:
    _, message_id = await seed_delete_log_vote(async_session_factory, pass_threshold=1, fail_threshold=-1)

    option_id, emoji = ("approve", "👍") if desired_weight > 0 else ("deny", "👎")
    mutation = await repository.cast_vote(message_id, 7, 70, GUILD_ID, option_id, emoji, abs(desired_weight))

    assert mutation is not None
    assert mutation.just_closed
    assert mutation.session.status is VoteStatus.CLOSED
    assert mutation.session.result is expected_result


async def test_concurrent_votes_report_exactly_one_closure(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Row-level locking, not the application, is what makes the second vote a no-op."""
    _, message_id = await seed_delete_log_vote(async_session_factory, pass_threshold=1, fail_threshold=-10)

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
    assert snapshot.status is VoteStatus.CLOSED
    assert len(snapshot.votes) == 1


async def test_generic_poll_persists_choices_tallies_and_due_closure(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, message_id = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility=VoteVisibility.ANONYMOUS_HIDDEN,
        # Already past, so the poll is due the moment it opens.
        deadline=Instant.now().subtract(seconds=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=GUILD_ID, label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=GUILD_ID, label="Two"),
        ),
    )
    assert message_id is not None

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
    assert closed.session.status is VoteStatus.CLOSED
    assert await repository.list_due(Instant.now()) == []


async def test_generic_polls_store_null_thresholds_rather_than_sentinels(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Read with raw SQL because the domain refuses to represent the sentinels at all."""
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=GUILD_ID, label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=GUILD_ID, label="Two"),
        ),
    )

    async with async_session_factory() as session:
        thresholds = (
            (
                await session.execute(
                    text("SELECT pass_threshold, fail_threshold FROM vote_sessions WHERE id = :id"),
                    {"id": session_id},
                )
            )
            .tuples()
            .one()
        )

    assert thresholds == (None, None)


async def test_a_poll_can_be_created_before_it_belongs_to_any_guild(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Creation must not require the transport that will eventually publish it."""
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Guild-free",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", label="Two"),
        ),
        guild_id=None,
        attach=False,
    )

    snapshot = await repository.get_by_id(session_id)

    assert snapshot is not None
    assert snapshot.poll is not None
    assert snapshot.poll.guild_id is None
    assert snapshot.messages == ()


async def test_a_guild_less_poll_becomes_addressable_once_a_card_is_attached(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Creation and publication are separate steps, so this is the second one."""
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Attach me",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", label="Two"),
        ),
        guild_id=None,
        attach=False,
    )
    assert await repository.get_by_message(7_001) is None

    async with async_session_factory.begin() as session:
        await attach_vote_message(session, message_id=7_001, vote_session_id=session_id)

    snapshot = await repository.get_by_message(7_001)
    assert snapshot is not None
    assert snapshot.id == session_id
    assert snapshot.message_ids == (7_001,)


async def test_a_poll_shown_in_two_places_tracks_both_locations(
    repository: VoteRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, message_id = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Two homes",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=GUILD_ID, label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=GUILD_ID, label="Two"),
        ),
    )
    assert message_id is not None

    async with async_session_factory.begin() as session:
        await attach_vote_message(session, message_id=8_002, vote_session_id=session_id, channel_id=CHANNEL_ID + 1)

    snapshot = await repository.get_by_id(session_id)
    assert snapshot is not None
    assert sorted(snapshot.message_ids) == sorted((message_id, 8_002))


@pytest.mark.parametrize(
    ("kind", "pass_threshold", "fail_threshold"),
    [
        ("generic", 3, -3),
        ("build", None, None),
        ("build", -1, -3),
        ("build", 3, 3),
    ],
)
async def test_the_schema_rejects_kind_threshold_combinations_the_domain_forbids(
    async_session_factory: async_sessionmaker[AsyncSession],
    kind: str,
    pass_threshold: int | None,
    fail_threshold: int | None,
) -> None:
    """Direct SQL on purpose: this asserts the database constraint, not the repository.

    The repository cannot produce these rows, which is exactly why the constraint has
    to be tested at the level that would still accept them.
    """
    with pytest.raises(IntegrityError):
        async with async_session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO vote_sessions (status, result, author_account_id, kind,"
                    " pass_threshold, fail_threshold)"
                    " VALUES ('open', 'pending', :author, :kind, :pass_threshold, :fail_threshold)"
                ),
                {
                    "author": AUTHOR_ACCOUNT_IDS[0],
                    "kind": kind,
                    "pass_threshold": pass_threshold,
                    "fail_threshold": fail_threshold,
                },
            )
