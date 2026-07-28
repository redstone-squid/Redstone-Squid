import asyncio
from collections.abc import AsyncGenerator, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.db.repos.vote_repository import SQLAlchemyVoteRepository
from squid.services.votes import StoredVoteMutation, VoteTarget

pytestmark = pytest.mark.integration

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
    weight DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (vote_session_id, user_id)
);
"""

_DROP_SCHEMA = """
DROP TABLE IF EXISTS votes, messages, build_vote_sessions, delete_log_vote_sessions, vote_sessions CASCADE;
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
                    INSERT INTO votes (vote_session_id, user_id, weight)
                    VALUES (:vote_session_id, :user_id, :weight)
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
    repository = SQLAlchemyVoteRepository(async_session_factory)
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
    repository = SQLAlchemyVoteRepository(async_session_factory)

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
    repository = SQLAlchemyVoteRepository(async_session_factory)

    snapshot = await repository.get_by_message(message_id)

    assert snapshot is not None
    assert snapshot.id == vote_session_id
    assert snapshot.votes == {7: 1.0, 8: -1.0}
    assert snapshot.message_ids == (message_id,)
    assert snapshot.target == VoteTarget(message_id=501, channel_id=502, server_id=503)


async def test_cast_vote_replaces_then_toggles_the_same_choice(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, message_id = await seed_delete_log_vote(
        async_session_factory,
        pass_threshold=10,
        fail_threshold=-10,
    )
    repository = SQLAlchemyVoteRepository(async_session_factory)

    inserted = await repository.cast_vote(message_id, 7, 1.0)
    replaced = await repository.cast_vote(message_id, 7, -1.0)
    removed = await repository.cast_vote(message_id, 7, -1.0)

    assert inserted is not None
    assert (inserted.previous_weight, inserted.current_weight) == (None, 1.0)
    assert replaced is not None
    assert (replaced.previous_weight, replaced.current_weight) == (1.0, -1.0)
    assert removed is not None
    assert (removed.previous_weight, removed.current_weight) == (-1.0, None)
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
    repository = SQLAlchemyVoteRepository(async_session_factory)

    mutation = await repository.cast_vote(message_id, 7, desired_weight)

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
    repository = SQLAlchemyVoteRepository(async_session_factory)

    results = await asyncio.gather(
        repository.cast_vote(message_id, 7, 1.0),
        repository.cast_vote(message_id, 8, 1.0),
    )

    mutations = [result for result in results if isinstance(result, StoredVoteMutation)]
    assert len(mutations) == 1
    assert mutations[0].just_closed
    assert sum(result is None for result in results) == 1

    snapshot = await repository.get_by_message(message_id)
    assert snapshot is not None
    assert snapshot.status == "closed"
    assert len(snapshot.votes) == 1
