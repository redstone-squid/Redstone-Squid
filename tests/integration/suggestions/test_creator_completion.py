"""Creator typeahead against live PostgreSQL.

Two things can only be checked against a real server. The prefix has to be folded the same way
the stored `normalized_name` is, or a query in the wrong case silently misses every creator; and
the prefix index has to actually be chosen, because the fallback is a sequential scan over every
alias on every keystroke.
"""

# ruff: noqa: RUF001  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.domain import ClaimMethod
from squid.accounts.infrastructure.models import Account, CreatorAlias
from squid.persistence.base import Base
from squid.suggestions.infrastructure.repository import PostgresSuggestionRepository

pytestmark = pytest.mark.asyncio

_TABLES = [Base.metadata.tables["accounts"], Base.metadata.tables["creator_aliases"]]


@pytest.fixture
async def creator_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    # `creator_aliases_normalized_name_prefix_idx` is declared on the model, so `create_all`
    # brings it along; the EXPLAIN test below depends on that.
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """A few interesting names plus filler, so the planner has a reason to use an index."""
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add_all(
            [
                CreatorAlias(name="Notch"),
                CreatorAlias(name="ΣΣ"),
                CreatorAlias(name="Straße"),
                CreatorAlias(
                    name="Ａlice",
                    account_id=account.id,
                    claimed_at=Instant.now(),
                    claim_method=ClaimMethod.VERIFIED_IGN,
                ),
            ]
        )
        session.add_all(CreatorAlias(name=f"Builder {index}") for index in range(2_000))


@pytest.mark.usefixtures("creator_tables")
async def test_prefix_matches_regardless_of_typed_case(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = PostgresSuggestionRepository(async_session_factory)

    for typed in ("Notch", "notch", "NOTCH", "  notc"):
        names = [name for name, _claimed in await repository.creators(typed, limit=10)]
        assert "Notch" in names, f"{typed!r} did not suggest Notch"


@pytest.mark.usefixtures("creator_tables")
async def test_prefix_matches_across_compatibility_and_case_folding(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The query is folded by `fold_creator_name`, so it reaches names `casefold` unified."""
    await seed(async_session_factory)
    repository = PostgresSuggestionRepository(async_session_factory)

    assert [name for name, _ in await repository.creators("Σς", limit=10)] == ["ΣΣ"]
    assert [name for name, _ in await repository.creators("strasse", limit=10)] == ["Straße"]
    assert [name for name, _ in await repository.creators("alice", limit=10)] == ["Ａlice"]


@pytest.mark.usefixtures("creator_tables")
async def test_claimed_flag_is_reported(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = PostgresSuggestionRepository(async_session_factory)

    assert await repository.creators("Ａlice", limit=10) == [("Ａlice", True)]
    assert await repository.creators("Notch", limit=10) == [("Notch", False)]


@pytest.mark.usefixtures("creator_tables")
async def test_results_are_bounded(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = PostgresSuggestionRepository(async_session_factory)

    assert len(await repository.creators("builder", limit=5)) == 5


@pytest.mark.usefixtures("creator_tables")
async def test_wildcards_in_the_query_are_escaped(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`%` is a literal a user can type, not a match-anything the typeahead should honour."""
    await seed(async_session_factory)
    repository = PostgresSuggestionRepository(async_session_factory)

    assert await repository.creators("%", limit=10) == []
    assert await repository.creators("Buil%er", limit=10) == []


@pytest.mark.usefixtures("creator_tables")
async def test_the_prefix_query_uses_the_prefix_index(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Folding in Python is what lets this stay a plain indexable prefix scan.

    Had the fold moved into SQL, the predicate would have been
    `normalized_name LIKE lower(btrim($1)) || '%'`, whose prefix the planner has to fold
    before it can derive index bounds.
    """
    await seed(async_session_factory)
    async with async_session_factory() as session:
        plan = "\n".join(
            row
            for row in (
                await session.scalars(
                    text(
                        "EXPLAIN SELECT name, account_id FROM creator_aliases "
                        "WHERE normalized_name LIKE 'builder 1%' "
                        "ORDER BY normalized_name LIMIT 10"
                    )
                )
            ).all()
        )
    assert "creator_aliases_normalized_name_prefix_idx" in plan, plan
    assert "Seq Scan" not in plan, plan
