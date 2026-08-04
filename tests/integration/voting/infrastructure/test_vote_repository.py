import asyncio
from collections.abc import AsyncGenerator, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.voting.domain import DEFAULT_VOTE_OPTIONS, StoredVoteMutation, VoteChoice, VoteOption, VoteTarget
from squid.voting.infrastructure.repository import VoteRepository

_CREATE_SCHEMA = """
CREATE TABLE vote_sessions (
    id BIGSERIAL PRIMARY KEY,
    status VARCHAR NOT NULL,
    result VARCHAR NOT NULL,
    author_id BIGINT NOT NULL,
    kind VARCHAR NOT NULL,
    pass_threshold INTEGER NOT NULL,
    fail_threshold INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE vote_session_options (
    vote_session_id BIGINT REFERENCES vote_sessions(id) ON DELETE CASCADE,
    identifier VARCHAR NOT NULL,
    guild_id BIGINT NOT NULL DEFAULT 0,
    emoji VARCHAR NOT NULL,
    choice VARCHAR NOT NULL CHECK (choice IN ('approve', 'deny', 'generic')),
    label VARCHAR,
    multiplier DOUBLE PRECISION NOT NULL,
    position SMALLINT NOT NULL,
    PRIMARY KEY (vote_session_id, guild_id, emoji),
    UNIQUE (vote_session_id, guild_id, position),
    CHECK (
        multiplier > 0
        AND multiplier != 'Infinity'::double precision
        AND multiplier != 'NaN'::double precision
    ),
    CHECK (position >= 0)
);
CREATE TABLE delete_log_vote_sessions (
    vote_session_id BIGINT PRIMARY KEY REFERENCES vote_sessions(id) ON DELETE CASCADE,
    target_message_id BIGINT NOT NULL,
    target_channel_id BIGINT NOT NULL,
    target_server_id BIGINT NOT NULL
);
CREATE TABLE build_vote_sessions (
    vote_session_id BIGINT PRIMARY KEY REFERENCES vote_sessions(id) ON DELETE CASCADE,
    build_id BIGINT NOT NULL,
    changes JSONB NOT NULL
);
CREATE TABLE generic_vote_sessions (
    vote_session_id BIGINT PRIMARY KEY REFERENCES vote_sessions(id) ON DELETE CASCADE,
    guild_id BIGINT NOT NULL,
    question VARCHAR NOT NULL,
    visibility VARCHAR NOT NULL,
    deadline TIMESTAMPTZ NOT NULL
);
CREATE TABLE messages (
    id BIGINT PRIMARY KEY,
    server_id BIGINT NOT NULL,
    channel_id BIGINT,
    author_id BIGINT NOT NULL,
    purpose VARCHAR NOT NULL,
    content VARCHAR,
    build_id BIGINT,
    vote_session_id BIGINT REFERENCES vote_sessions(id),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE votes (
    vote_session_id BIGINT REFERENCES vote_sessions(id) ON DELETE CASCADE,
    user_id BIGINT,
    guild_id BIGINT NOT NULL,
    option_id VARCHAR NOT NULL,
    emoji VARCHAR NOT NULL,
    weight DOUBLE PRECISION NOT NULL CHECK (weight > 0),
    PRIMARY KEY (vote_session_id, user_id)
);
"""

_DROP_SCHEMA = """
DROP TABLE IF EXISTS
    votes, messages, generic_vote_sessions, build_vote_sessions, delete_log_vote_sessions,
    vote_session_options, vote_sessions CASCADE;
"""


@pytest.fixture(autouse=True)
async def vote_schema(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create the minimal production-shaped schema needed by the custom repository."""
    async with async_engine.begin() as connection:
        for statement in _CREATE_SCHEMA.strip().split(";"):
            if statement.strip():
                await connection.execute(text(statement))
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.execute(text(_DROP_SCHEMA))


async def seed_delete_log_vote(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    pass_threshold: int = 3,
    fail_threshold: int = -3,
    votes: Mapping[int, float] | None = None,
) -> tuple[int, int]:
    """Persist an open delete-log vote session and return its session/message IDs."""
    async with session_factory.begin() as session:
        vote_session_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO vote_sessions
                        (status, result, author_id, kind, pass_threshold, fail_threshold)
                    VALUES
                        ('open', 'pending', 99, 'delete_log', :pass_threshold, :fail_threshold)
                    RETURNING id
                    """
                ),
                {"pass_threshold": pass_threshold, "fail_threshold": fail_threshold},
            )
        ).scalar_one()
        message_id = 1_000 + vote_session_id
        await session.execute(
            text(
                """
                INSERT INTO vote_session_options
                    (vote_session_id, identifier, guild_id, emoji, choice, multiplier, position)
                VALUES
                    (:vote_session_id, 'approve', 0, '👍', 'approve', 1.0, 0),
                    (:vote_session_id, 'approve', 0, '✅', 'approve', 1.0, 1),
                    (:vote_session_id, 'deny', 0, '👎', 'deny', 1.0, 2),
                    (:vote_session_id, 'deny', 0, '❌', 'deny', 1.0, 3)
                """
            ),
            {"vote_session_id": vote_session_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO delete_log_vote_sessions
                    (vote_session_id, target_message_id, target_channel_id, target_server_id)
                VALUES
                    (:vote_session_id, 501, 502, 503)
                """
            ),
            {"vote_session_id": vote_session_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO messages
                    (id, server_id, channel_id, author_id, purpose, content, vote_session_id)
                VALUES
                    (:message_id, 503, 502, 99, 'vote', 'Vote now', :vote_session_id)
                """
            ),
            {"message_id": message_id, "vote_session_id": vote_session_id},
        )
        if votes:
            await session.execute(
                text(
                    """
                    INSERT INTO votes (vote_session_id, user_id, guild_id, option_id, emoji, weight)
                    VALUES (
                        :vote_session_id,
                        :user_id,
                        503,
                        CASE WHEN :weight < 0 THEN 'deny' ELSE 'approve' END,
                        CASE WHEN :weight < 0 THEN '👎' ELSE '👍' END,
                        abs(:weight)
                    )
                    """
                ),
                [
                    {"vote_session_id": vote_session_id, "user_id": user_id, "weight": weight}
                    for user_id, weight in votes.items()
                ],
            )
    return vote_session_id, message_id


async def test_vote_aggregates_are_persisted_by_repository(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)
    build_session_id = await repository.create_build_session(
        author_id=99,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=[("submission_status", "pending", "confirmed")],
    )
    delete_session_id = await repository.create_delete_log_session(
        author_id=100,
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
                        SELECT author_id, kind, pass_threshold, fail_threshold, status, result
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


async def test_target_failure_rolls_back_vote_root(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = VoteRepository(async_session_factory)

    with pytest.raises(StatementError):
        await repository.create_build_session(
            author_id=99,
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
        author_id=100,
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

    inserted = await repository.cast_vote(message_id, 7, 503, "approve", "👍", 1.0)
    replaced = await repository.cast_vote(message_id, 7, 503, "deny", "👎", 1.0)
    removed = await repository.cast_vote(message_id, 7, 503, "deny", "👎", 1.0)

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
    mutation = await repository.cast_vote(message_id, 7, 503, option_id, emoji, abs(desired_weight))

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
        repository.cast_vote(message_id, 7, 503, "approve", "👍", 1.0),
        repository.cast_vote(message_id, 8, 503, "approve", "👍", 1.0),
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
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", guild_id=503, label="One"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", guild_id=503, label="Two"),
    )
    deadline = Instant.now().subtract(seconds=1)
    session_id = await repository.create_generic_session(
        author_id=99,
        guild_id=503,
        question="Choose one",
        visibility="anonymous_hidden",
        deadline=deadline,
        options=options,
    )
    message_id = 10_000 + session_id
    async with async_session_factory.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO messages (id, server_id, channel_id, author_id, purpose, vote_session_id)
                VALUES (:message_id, 503, 502, 99, 'vote', :session_id)
                """
            ),
            {"message_id": message_id, "session_id": session_id},
        )

    await repository.cast_vote(message_id, 7, 503, "one", "1️⃣", 3)
    await repository.cast_vote(message_id, 8, 503, "two", "2️⃣", 1)
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
