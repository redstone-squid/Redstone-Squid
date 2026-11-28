"""Creator typeahead uses the stored Unicode folding and an indexable prefix query.

The EXPLAIN test checks a representative query against a real PostgreSQL planner.
"""

# ruff: noqa: RUF001  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account, CreatorAlias
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.suggestions.application.queries import SuggestionQueryService
from tests.support.creator_credits import publish_credits

pytestmark = pytest.mark.asyncio


@pytest.fixture
def async_session_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    return migrated_session_factory


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
    await publish_credits(session_factory, ["Notch", "ΣΣ", "Straße", *(f"Builder {i}" for i in range(12))])
    await publish_credits(session_factory, ["Ａlice"], owner=account.id)


async def test_prefix_matches_regardless_of_typed_case(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    for typed in ("Notch", "notch", "NOTCH", "  notc"):
        names = await repository.creators(typed, limit=10)
        assert "Notch" in names, f"{typed!r} did not suggest Notch"


async def test_prefix_matches_across_compatibility_and_case_folding(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The query is folded by `fold_creator_name`, so it reaches names `casefold` unified."""
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert await repository.creators("Σς", limit=10) == ["ΣΣ"]
    assert await repository.creators("strasse", limit=10) == ["Straße"]
    assert await repository.creators("alice", limit=10) == ["Ａlice"]


@pytest.mark.parametrize("typed", ["alice", "ＡL"])
async def test_public_creator_profile_prefix_uses_the_same_compatibility_fold(
    async_session_factory: async_sessionmaker[AsyncSession],
    typed: str,
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    profiles = await repository.creator_profiles(typed, limit=10)

    assert [name for _public_id, name in profiles] == ["Ａlice"]


async def test_public_profile_search_matches_every_alias_and_escapes_wildcards(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        public_id = str(account.public_account_id)
    await publish_credits(async_session_factory, ["Alpha", "Zulu"], owner=account.id)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    assert await repository.creator_profiles("zul", limit=10) == [(public_id, "Zulu")]
    assert await repository.creator_profiles("%", limit=10) == []
    assert len(await repository.creator_profiles("", limit=10)) == 1


async def test_name_choices_do_not_imply_account_ownership(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert await repository.creators("Ａlice", limit=10) == ["Ａlice"]
    assert await repository.creators("Notch", limit=10) == ["Notch"]


async def test_results_are_bounded(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert len(await repository.creators("builder", limit=5)) == 5


async def test_wildcards_in_the_query_are_escaped(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`%` is a literal a user can type, not a match-anything the typeahead should honour."""
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert await repository.creators("%", limit=10) == []
    assert await repository.creators("Buil%er", limit=10) == []


async def test_the_prefix_query_uses_the_prefix_index(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A pre-folded prefix leaves normalized_name directly searchable through its prefix index."""
    await seed(async_session_factory)
    async with async_session_factory.begin() as session:
        session.add_all(CreatorAlias(name=f"Filler {index}") for index in range(2000))
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
