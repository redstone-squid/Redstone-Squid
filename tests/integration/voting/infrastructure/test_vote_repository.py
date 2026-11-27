import asyncio
from collections.abc import AsyncGenerator
from functools import partial

import pytest
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from anyio import create_task_group
from sqlalchemy import Table, insert, text, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account, AccountProfile
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.errors import DataIntegrityError
from squid.core.messages.models import Message
from squid.core.pagination import FIRST_PAGE
from squid.core.persistence.alembic_entities import current_entities
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.posts.models import DiscordPost
from squid.core.settings.models import ServerSetting
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.voting.domain import (
    DEFAULT_VOTE_MENU,
    DeleteLogVoteTarget,
    ReviewFilters,
    ReviewKind,
    ReviewSort,
    StoredVoteMutation,
    VoteAlias,
    VoteChoice,
    VoteKind,
    VoteMenu,
    VoteOption,
    VoteSessionResult,
    VoteStatus,
    VoteVisibility,
)
from squid.core.voting.models import (
    BuildVoteSession,
    DeleteLogVoteSession,
    GenericVoteSession,
    Vote,
    VoteSession,
    VoteSessionOption,
    VoteSessionOptionAlias,
)
from tests.support.schema import model_tables, with_foreign_key_targets
from tests.support.unit_of_work import transact
from tests.support.voting import (
    AUTHOR_ACCOUNT_IDS,
    BUILD_ID,
    CHANNEL_ID,
    GUILD_ID,
    TARGET_MESSAGE_ID,
    VOTER_ACCOUNT_IDS,
    attach_vote_message,
    generic_menu,
    record_vote_message,
    seed_delete_log_vote,
)

_TABLES: tuple[Table, ...] = with_foreign_key_targets(
    *model_tables(
        VoteSession,
        VoteSessionOption,
        VoteSessionOptionAlias,
        BuildVoteSession,
        DeleteLogVoteSession,
        GenericVoteSession,
        Vote,
        Message,
        DiscordPost,
        AccountProfile,
    )
)

_KIND_SUBTYPE_ENTITIES = {
    "enforce_vote_session_kind_subtype",
    "vote_sessions_kind_subtype_check",
    "build_vote_sessions_kind_subtype_check",
    "delete_log_vote_sessions_kind_subtype_check",
    "generic_vote_sessions_kind_subtype_check",
}


def _kind_subtype_sql() -> list[str]:
    statements = [
        entity.to_sql_statement_create().text
        for entity in current_entities()
        if isinstance(entity, PGFunction | PGTrigger) and entity.signature.partition("(")[0] in _KIND_SUBTYPE_ENTITIES
    ]
    assert len(statements) == len(_KIND_SUBTYPE_ENTITIES)
    return statements


@pytest.fixture(autouse=True)
async def vote_schema(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    """Create voting tables and foreign-key targets from mapped metadata.

    Using the model definitions keeps repository tests aligned with the shipped columns
    and constraints, including references to accounts, guilds, and builds.
    Install the deferred kind/subtype functions and triggers separately because
    metadata.create_all does not create those managed PostgreSQL entities.
    """
    async with async_engine.begin() as connection:
        # `builds` is in the closure through build_vote_sessions.build_id, and it carries a
        # pgvector column, so the type has to exist before the tables are created.
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
        for statement in _kind_subtype_sql():
            await connection.execute(text(statement))
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))
            await connection.execute(text("DROP FUNCTION public.enforce_vote_session_kind_subtype()"))


@pytest.fixture(autouse=True)
async def referenced_rows(vote_schema: None, async_session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Seed fixed account, guild, and build IDs required by vote foreign keys."""
    async with async_session_factory.begin() as session:
        await session.execute(
            insert(Account).values([{"id": account_id} for account_id in (*AUTHOR_ACCOUNT_IDS, *VOTER_ACCOUNT_IDS)])
        )
        await session.execute(insert(ServerSetting).values(server_id=GUILD_ID))
        await session.execute(
            insert(Build).values(
                id=BUILD_ID,
                category="Other",
                submission_status=BuildStatus.PENDING,
                submitter_account_id=AUTHOR_ACCOUNT_IDS[0],
            )
        )


@pytest.fixture
def repository(async_session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, async_session_factory)


async def seed_generic_poll(
    session_factory: async_sessionmaker[AsyncSession],
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    *,
    question: str,
    visibility: VoteVisibility,
    deadline: Instant,
    menu: VoteMenu,
    guild_id: int | None = GUILD_ID,
    attach: bool = True,
) -> tuple[int, int | None]:
    """Create a poll and return its session ID and optional attached message ID."""
    session_id = await transact(
        repository,
        lambda uow: uow.votes.create_generic_session(
            author_account_id=AUTHOR_ACCOUNT_IDS[0],
            question=question,
            visibility=visibility,
            deadline=deadline,
            menu=menu,
            guild_id=guild_id,
        ),
        commit=True,
    )
    if not attach:
        return session_id, None
    message_id = 10_000 + session_id
    async with session_factory.begin() as session:
        await attach_vote_message(session, message_id=message_id, vote_session_id=session_id)
    return session_id, message_id


async def cast_ballot(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    session_id: int,
    account_id: int,
    option_id: str,
    emoji: str,
    weight: float,
) -> StoredVoteMutation | None:
    """Resolve the option the way VoteService does, then cast through the repository."""
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    option = snapshot.menu.option(option_id)
    assert option is not None
    assert snapshot.menu.alias_for(option_id, GUILD_ID, emoji=emoji) is not None
    return await transact(
        repository,
        lambda uow: uow.votes.cast_vote(session_id, account_id, option, weight, guild_id=GUILD_ID, emoji=emoji),
        commit=True,
    )


async def test_vote_aggregates_are_persisted_by_repository(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_session_id = await transact(
        repository,
        lambda uow: uow.votes.create_build_session(
            author_account_id=99,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=BUILD_ID,
            changes=[("submission_status", "pending", "confirmed")],
        ),
        commit=True,
    )
    delete_session_id = await transact(
        repository,
        lambda uow: uow.votes.create_delete_log_session(
            author_account_id=100,
            pass_threshold=4,
            fail_threshold=-2,
            message_id=TARGET_MESSAGE_ID,
            channel_id=CHANNEL_ID,
            server_id=GUILD_ID,
        ),
        commit=True,
    )

    build = await transact(repository, lambda uow: uow.votes.get_by_id(build_session_id))
    delete = await transact(repository, lambda uow: uow.votes.get_by_id(delete_session_id))

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
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await transact(
        repository,
        lambda uow: uow.votes.get_or_create_build_submission_session(
            author_account_id=99,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=BUILD_ID,
            changes=[("submission_status", "pending", "confirmed")],
        ),
        commit=True,
    )
    second = await transact(
        repository,
        lambda uow: uow.votes.get_or_create_build_submission_session(
            author_account_id=100,
            pass_threshold=4,
            fail_threshold=-2,
            build_id=BUILD_ID,
            changes=[("submission_status", "pending", "confirmed")],
        ),
        commit=True,
    )

    async with async_session_factory() as session:
        roots = await session.scalar(text("SELECT count(*) FROM vote_sessions"))

    assert second == first
    assert roots == 1


async def test_target_failure_rolls_back_vote_root(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Use an existing build so unserializable target data causes the StatementError.

    A missing foreign-key target could raise the same exception without reaching
    the write whose rollback this test checks.
    """
    with pytest.raises(StatementError):
        await transact(
            repository,
            lambda uow: uow.votes.create_build_session(
                author_account_id=99,
                pass_threshold=3,
                fail_threshold=-3,
                build_id=BUILD_ID,
                changes=[("invalid", object(), object())],
            ),
            commit=True,
        )

    async with async_session_factory() as session:
        count = (await session.execute(text("SELECT count(*) FROM vote_sessions"))).scalar_one()
    assert count == 0


async def test_get_by_message_maps_votes_messages_and_delete_target(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vote_session_id, message_id = await seed_delete_log_vote(async_session_factory, votes={7: 1.0, 8: -1.0})

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_message(message_id))

    assert snapshot is not None
    assert snapshot.id == vote_session_id
    assert snapshot.votes == {7: 1.0, 8: -1.0}
    assert [message.id for message in snapshot.messages] == [message_id]
    assert snapshot.menu == DEFAULT_VOTE_MENU
    assert snapshot.target == DeleteLogVoteTarget(TARGET_MESSAGE_ID, CHANNEL_ID, GUILD_ID)


async def test_custom_vote_options_are_persisted_in_order(repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> None:
    menu = VoteMenu(
        (
            VoteOption("strong_approve", VoteChoice.APPROVE, multiplier=2.0),
            VoteOption("deny", VoteChoice.DENY, position=1),
        ),
        (VoteAlias("strong_approve", "<:strong_yes:123>"), VoteAlias("deny", "👎", position=1)),
    )

    vote_session_id = await transact(
        repository,
        lambda uow: uow.votes.create_delete_log_session(
            author_account_id=100,
            pass_threshold=4,
            fail_threshold=-2,
            message_id=TARGET_MESSAGE_ID,
            channel_id=CHANNEL_ID,
            server_id=GUILD_ID,
            menu=menu,
        ),
        commit=True,
    )

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(vote_session_id))

    assert snapshot is not None
    assert snapshot.menu == menu


async def test_cast_vote_replaces_then_toggles_the_same_choice(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=10, fail_threshold=-10)

    inserted = await cast_ballot(repository, session_id, 7, "approve", "👍", 1.0)
    replaced = await cast_ballot(repository, session_id, 7, "deny", "👎", 1.0)
    removed = await cast_ballot(repository, session_id, 7, "deny", "👎", 1.0)

    assert inserted is not None
    assert (inserted.previous_weight, inserted.current_weight) == (None, 1.0)
    assert replaced is not None
    assert (replaced.previous_weight, replaced.current_weight) == (1.0, 1.0)
    assert removed is not None
    assert (removed.previous_weight, removed.current_weight) == (1.0, None)
    assert removed.session.votes == {}


async def test_selecting_the_stored_option_again_keeps_the_ballot(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A recount selects rather than toggles, so repeating it cannot withdraw a ballot."""
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=10, fail_threshold=-10)
    await cast_ballot(repository, session_id, 7, "approve", "👍", 1.0)
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    option = snapshot.menu.option("approve")
    assert option is not None

    kept = await transact(
        repository,
        lambda uow: uow.votes.cast_vote(session_id, 7, option, 2.0, guild_id=GUILD_ID, emoji="👍", toggle=False),
        commit=True,
    )

    assert kept is not None
    assert (kept.previous_weight, kept.current_weight) == (1.0, 2.0)
    assert set(kept.session.votes) == {7}


@pytest.mark.parametrize(
    ("option_id", "emoji", "expected_result"),
    [
        pytest.param("approve", "👍", VoteSessionResult.APPROVED, id="approved"),
        pytest.param("deny", "👎", VoteSessionResult.DENIED, id="denied"),
    ],
)
async def test_threshold_closure_freezes_the_deciding_ballot(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
    option_id: str,
    emoji: str,
    expected_result: VoteSessionResult,
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=1, fail_threshold=-1)

    mutation = await cast_ballot(repository, session_id, 7, option_id, emoji, 1.0)

    assert mutation is not None
    assert mutation.just_closed
    assert mutation.session.status is VoteStatus.CLOSED
    assert mutation.session.result is expected_result

    assert await cast_ballot(repository, session_id, 8, "deny", "👎", 4.0) is None
    assert await transact(repository, lambda uow: uow.votes.get_by_id(session_id)) == mutation.session


async def test_concurrent_votes_report_exactly_one_closure(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Both transactions target one row; only the vote that closes it may be recorded."""
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=1, fail_threshold=-10)

    results = await asyncio.gather(
        cast_ballot(repository, session_id, 7, "approve", "👍", 1.0),
        cast_ballot(repository, session_id, 8, "approve", "👍", 1.0),
    )

    mutations = [result for result in results if isinstance(result, StoredVoteMutation)]
    assert len(mutations) == 1
    assert mutations[0].just_closed
    assert sum(result is None for result in results) == 1

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    assert snapshot.status is VoteStatus.CLOSED
    assert len(snapshot.votes) == 1


async def test_generic_poll_persists_choices_tallies_and_due_closure(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility=VoteVisibility.ANONYMOUS_HIDDEN,
        # Already past, so the poll is due the moment it opens.
        deadline=Instant.now().subtract(seconds=1),
        menu=generic_menu(GUILD_ID),
    )

    await cast_ballot(repository, session_id, 7, "one", "1️⃣", 3)
    await cast_ballot(repository, session_id, 8, "two", "2️⃣", 1)
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))

    assert snapshot is not None
    assert snapshot.poll is not None
    assert snapshot.poll.question == "Choose one"
    assert snapshot.raw_tallies() == {"one": 1, "two": 1}
    assert snapshot.weighted_tallies() == {"one": 3, "two": 1}
    assert [item.id for item in await transact(repository, lambda uow: uow.votes.list_due(Instant.now()))] == [
        session_id
    ]

    closed = await transact(repository, lambda uow: uow.votes.close(session_id), commit=True)
    assert closed is not None
    assert closed.just_closed
    assert closed.session.status is VoteStatus.CLOSED
    assert await transact(repository, lambda uow: uow.votes.list_due(Instant.now())) == []
    assert await cast_ballot(repository, session_id, 8, "one", "1️⃣", 4) is None
    assert await transact(repository, lambda uow: uow.votes.get_by_id(session_id)) == closed.session


async def test_generic_polls_store_null_thresholds_rather_than_sentinels(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Inspect persisted thresholds directly, without relying on domain reconstruction."""
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(GUILD_ID),
    )

    async with async_session_factory() as session:
        thresholds = (
            await session.execute(
                text("SELECT pass_threshold, fail_threshold FROM vote_sessions WHERE id = :id"),
                {"id": session_id},
            )
        ).one()

    assert thresholds == (None, None)


async def test_a_poll_in_a_guild_without_settings_creates_the_settings_row(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A guild joined while the bot was offline has no settings row until something needs one."""
    unseen_guild_id = GUILD_ID + 1
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="New guild",
        visibility=VoteVisibility.VISIBLE_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
        guild_id=unseen_guild_id,
        attach=False,
    )

    async with async_session_factory() as session:
        assert await session.get(ServerSetting, unseen_guild_id) is not None
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    assert snapshot.poll is not None
    assert snapshot.poll.guild_id == unseen_guild_id


async def test_a_poll_can_be_created_before_it_belongs_to_any_guild(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Creation must not require the transport that will eventually publish it."""
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Guild-free",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
        guild_id=None,
        attach=False,
    )

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))

    assert snapshot is not None
    assert snapshot.poll is not None
    assert snapshot.poll.guild_id is None
    assert snapshot.messages == ()


async def test_a_guild_less_poll_becomes_addressable_once_a_card_is_attached(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Attach me",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
        guild_id=None,
        attach=False,
    )
    assert await transact(repository, lambda uow: uow.votes.get_by_message(7_001)) is None

    async with async_session_factory.begin() as session:
        await record_vote_message(session, message_id=7_001)
    await transact(repository, lambda uow: uow.votes.attach_message(session_id, 7_001), commit=True)
    await transact(repository, lambda uow: uow.votes.attach_message(session_id, 7_001), commit=True)

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_message(7_001))
    assert snapshot is not None
    assert snapshot.id == session_id
    assert [message.id for message in snapshot.messages] == [7_001]


async def test_a_message_cannot_be_attached_to_two_vote_sessions(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="First",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
        guild_id=None,
        attach=False,
    )
    second_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Second",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
        guild_id=None,
        attach=False,
    )
    async with async_session_factory.begin() as session:
        await record_vote_message(session, message_id=7_002)

    await transact(repository, lambda uow: uow.votes.attach_message(first_id, 7_002), commit=True)
    with pytest.raises(DataIntegrityError, match="different resource"):
        await transact(repository, lambda uow: uow.votes.attach_message(second_id, 7_002), commit=True)


async def test_a_poll_shown_in_two_places_tracks_both_locations(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, message_id = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Two homes",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(GUILD_ID),
    )
    assert message_id is not None

    async with async_session_factory.begin() as session:
        await record_vote_message(session, message_id=8_002, channel_id=CHANNEL_ID + 1)
    await transact(repository, lambda uow: uow.votes.attach_message(session_id, 8_002), commit=True)

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    assert sorted(message.id for message in snapshot.messages) == sorted((message_id, 8_002))


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
    """Bypass domain validation with SQL to exercise the database constraint."""
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


async def _insert_vote_root(session: AsyncSession, kind: str) -> int:
    threshold = None if kind == "generic" else 3
    fail_threshold = None if kind == "generic" else -3
    return (
        await session.execute(
            text(
                "INSERT INTO vote_sessions (status, result, author_account_id, kind, pass_threshold, fail_threshold) "
                "VALUES ('open', 'pending', :author, :kind, :pass_threshold, :fail_threshold) RETURNING id"
            ),
            {
                "author": AUTHOR_ACCOUNT_IDS[0],
                "kind": kind,
                "pass_threshold": threshold,
                "fail_threshold": fail_threshold,
            },
        )
    ).scalar_one()


async def test_kind_and_subtype_can_transition_atomically(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vote_session_id = await transact(
        repository,
        lambda uow: uow.votes.create_build_session(
            author_account_id=AUTHOR_ACCOUNT_IDS[0], pass_threshold=3, fail_threshold=-3, build_id=BUILD_ID, changes=[]
        ),
        commit=True,
    )

    # The deferred invariant permits intermediate mismatches but requires a valid shape at commit.
    async with async_session_factory.begin() as session:
        await session.execute(
            text("DELETE FROM build_vote_sessions WHERE vote_session_id = :id"),
            {"id": vote_session_id},
        )
        await session.execute(
            text(
                "UPDATE vote_sessions SET kind = 'generic', pass_threshold = NULL, fail_threshold = NULL WHERE id = :id"
            ),
            {"id": vote_session_id},
        )
        await session.execute(
            text(
                "INSERT INTO generic_vote_sessions "
                "(vote_session_id, guild_id, question, visibility, scope, deadline) "
                "VALUES (:id, :guild_id, 'Choose', 'anonymous_live', 'guild', now() + interval '1 hour')"
            ),
            {"id": vote_session_id, "guild_id": GUILD_ID},
        )
        await session.execute(
            update(VoteSessionOption)
            .where(VoteSessionOption.vote_session_id == vote_session_id)
            .values(choice=VoteChoice.GENERIC, label=VoteSessionOption.identifier)
        )

    transitioned = await transact(repository, lambda uow: uow.votes.get_by_id(vote_session_id))
    assert transitioned is not None
    assert transitioned.kind is VoteKind.GENERIC
    assert transitioned.poll is not None
    assert transitioned.poll.question == "Choose"


@pytest.mark.parametrize("invalid_shape", ["missing", "mismatched", "multiple", "duplicate_build"])
async def test_deferred_kind_subtype_invariant_rejects_invalid_final_shapes(
    async_session_factory: async_sessionmaker[AsyncSession],
    invalid_shape: str,
) -> None:
    async def commit_invalid_shape() -> None:
        async with async_session_factory.begin() as session:
            kind = "generic" if invalid_shape == "missing" else "build"
            vote_session_id = await _insert_vote_root(session, kind)

            if invalid_shape in {"multiple", "duplicate_build"}:
                await session.execute(
                    text(
                        "INSERT INTO build_vote_sessions (vote_session_id, build_id, changes) "
                        "VALUES (:id, :build_id, CAST('[]' AS jsonb))"
                    ),
                    {"id": vote_session_id, "build_id": BUILD_ID},
                )
            if invalid_shape in {"mismatched", "multiple"}:
                await session.execute(
                    text(
                        "INSERT INTO delete_log_vote_sessions "
                        "(vote_session_id, target_message_id, target_channel_id, target_server_id) "
                        "VALUES (:id, 501, 200, :guild_id)"
                    ),
                    {"id": vote_session_id, "guild_id": GUILD_ID},
                )
            if invalid_shape == "duplicate_build":
                await session.execute(
                    insert(Build).values(
                        id=BUILD_ID + 1,
                        submission_status=BuildStatus.PENDING,
                        submitter_account_id=AUTHOR_ACCOUNT_IDS[0],
                    )
                )
                await session.execute(
                    text(
                        "INSERT INTO build_vote_sessions (vote_session_id, build_id, changes) "
                        "VALUES (:id, :build_id, CAST('[]' AS jsonb))"
                    ),
                    {"id": vote_session_id, "build_id": BUILD_ID + 1},
                )

    with pytest.raises(IntegrityError):
        await commit_invalid_shape()


async def test_changing_only_the_kind_cannot_strand_the_existing_subtype(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vote_session_id = await transact(
        repository,
        lambda uow: uow.votes.create_build_session(
            author_account_id=AUTHOR_ACCOUNT_IDS[0], pass_threshold=3, fail_threshold=-3, build_id=BUILD_ID, changes=[]
        ),
        commit=True,
    )

    with pytest.raises(IntegrityError):
        async with async_session_factory.begin() as session:
            await session.execute(
                text("UPDATE vote_sessions SET kind = 'delete_log' WHERE id = :id"),
                {"id": vote_session_id},
            )

    unchanged = await transact(repository, lambda uow: uow.votes.get_by_id(vote_session_id))
    assert unchanged is not None
    assert unchanged.kind is VoteKind.BUILD


async def test_ballot_keeps_the_card_and_alias_it_was_cast_with(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=2)
    await cast_ballot(repository, session_id, 7, "approve", "✅", 1.0)
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot is not None
    selection = snapshot.selection_for(7)
    assert selection is not None
    assert (selection.option_id, selection.guild_id, selection.emoji) == ("approve", GUILD_ID, "✅")


async def test_casting_an_option_the_session_does_not_store_is_an_integrity_error(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory)
    stranger = VoteOption("strong_approve", VoteChoice.APPROVE, multiplier=2.0)

    with pytest.raises(DataIntegrityError, match="not one of the session's options"):
        await transact(repository, lambda uow: uow.votes.cast_vote(session_id, 7, stranger, 1.0))


async def test_ballot_cannot_reference_an_option_its_session_lacks(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory)
    with pytest.raises(IntegrityError):
        async with async_session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO votes (vote_session_id, account_id, guild_id, option_identifier, weight) "
                    "VALUES (:session, 7, :guild, 'strong_approve', 1)"
                ),
                {"session": session_id, "guild": GUILD_ID},
            )


async def test_clearing_a_ballot_twice_keeps_it_absent(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=10, fail_threshold=-10)
    await cast_ballot(repository, session_id, 7, "approve", "👍", 1.0)
    removed = await transact(repository, lambda uow: uow.votes.clear_vote(session_id, 7), commit=True)
    repeated = await transact(repository, lambda uow: uow.votes.clear_vote(session_id, 7), commit=True)
    assert removed is not None
    assert removed.previous_weight == 1.0
    assert repeated is not None
    assert repeated.previous_weight is None
    assert repeated.session.votes == {}


async def test_concurrent_deletion_requests_share_one_session(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
) -> None:
    ids: list[int] = []

    async def create() -> None:
        ids.append(
            await transact(
                repository,
                lambda uow: uow.votes.create_delete_log_session(
                    author_account_id=AUTHOR_ACCOUNT_IDS[0],
                    pass_threshold=10,
                    fail_threshold=-10,
                    message_id=123456,
                    channel_id=123,
                    server_id=GUILD_ID,
                ),
                commit=True,
            )
        )

    async with create_task_group() as tasks:
        tasks.start_soon(create)
        tasks.start_soon(create)
    assert len(ids) == 2
    assert ids[0] == ids[1]
    await transact(repository, lambda uow: uow.votes.close(ids[0]), commit=True)
    await create()
    assert ids[2] == ids[0]


async def test_review_preserves_collected_ballots_and_rejects_stale_decisions(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await seed_generic_poll(
        async_session_factory,
        repository,
        question="Choose one",
        visibility=VoteVisibility.VISIBLE_LIVE,
        deadline=Instant.now(),
        menu=generic_menu(GUILD_ID),
    )
    await cast_ballot(repository, session_id, 7, "one", "1️⃣", 3)
    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))
    assert snapshot
    counting = await transact(
        repository, lambda uow: uow.votes.transition_poll(snapshot, VoteStatus.COUNTING, claim=True), commit=True
    )
    assert counting
    review = await transact(
        repository, lambda uow: uow.votes.transition_poll(counting, VoteStatus.REVIEW, unreadable=(100,)), commit=True
    )
    assert review
    assert review.poll
    assert review.poll.unreadable_cards == (100,)
    assert await transact(repository, lambda uow: uow.votes.pending_reviews(GUILD_ID)) == [session_id]
    assert await transact(repository, lambda uow: uow.votes.clear_vote(session_id, 7), commit=True) is None
    closed = await transact(
        repository,
        lambda uow: uow.votes.transition_poll(
            review, VoteStatus.CLOSED, actor_id=7, reason="Card deleted", unreadable=(100,)
        ),
        commit=True,
    )
    assert closed
    assert closed.poll
    assert closed.poll.voting_ended_at == review.poll.voting_ended_at
    assert closed.weighted_tallies() == {"one": 3}
    assert (
        await transact(
            repository,
            lambda uow: uow.votes.transition_poll(review, VoteStatus.OPEN, deadline=Instant.now().add(hours=1)),
            commit=True,
        )
        is None
    )


async def test_review_listing_pages_build_votes_in_creation_order_without_other_kinds(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def build_vote() -> int:
        return await transact(
            repository,
            lambda uow: uow.votes.create_build_session(
                author_account_id=99,
                pass_threshold=3,
                fail_threshold=-3,
                build_id=BUILD_ID,
                changes=[("submission_status", "pending", "confirmed")],
            ),
            commit=True,
        )

    older, closed, newer = await build_vote(), await build_vote(), await build_vote()
    await transact(repository, lambda uow: uow.votes.close(closed), commit=True)
    await seed_generic_poll(
        async_session_factory,
        repository,
        question="Not a review",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        deadline=Instant.now().add(hours=1),
        menu=generic_menu(None),
    )
    queue = ReviewFilters(status=VoteStatus.OPEN)

    first = await transact(
        repository, lambda uow: uow.votes.list_reviews(queue, sort=ReviewSort.OLDEST, selector=FIRST_PAGE, page_size=1)
    )
    assert first.next is not None
    after = await transact(
        repository,
        lambda uow: uow.votes.list_reviews(queue, sort=ReviewSort.OLDEST, selector=first.next.selector(), page_size=2),
    )
    newest = await transact(
        repository,
        lambda uow: uow.votes.list_reviews(ReviewFilters(), sort=ReviewSort.NEWEST, selector=FIRST_PAGE, page_size=5),
    )
    edits = await transact(
        repository,
        lambda uow: uow.votes.list_reviews(
            ReviewFilters(frozenset({ReviewKind.BUILD_EDIT})), sort=ReviewSort.NEWEST, selector=FIRST_PAGE, page_size=1
        ),
    )

    assert [item.id for item in first.items] == [older]
    assert first.total == 2
    assert [item.id for item in after.items] == [newer]
    assert [item.id for item in newest.items] == [newer, closed, older]
    assert edits.total == 0


async def test_ballots_carry_their_voters_public_names_but_not_hidden_ones(
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    shown, hidden = VOTER_ACCOUNT_IDS
    async with async_session_factory.begin() as session:
        session.add_all(
            [
                AccountProfile(account_id=shown, display_name="Alex"),
                AccountProfile(account_id=hidden, display_name="Sam", hidden=True),
            ]
        )
    session_id, _ = await seed_delete_log_vote(async_session_factory, pass_threshold=10, fail_threshold=-10)
    await cast_ballot(repository, session_id, shown, "approve", "👍", 1.0)
    await cast_ballot(repository, session_id, hidden, "deny", "👎", 1.0)

    snapshot = await transact(repository, lambda uow: uow.votes.get_by_id(session_id))

    assert snapshot is not None
    assert [(item.account_id, item.display_name) for item in snapshot.selections] == [(shown, "Alex"), (hidden, None)]
