"""Integration coverage for who computes `creator_aliases.normalized_name`.

The column stopped being `GENERATED ALWAYS AS (lower(btrim(name)))` because Postgres and Python
could not be made to agree on it, and the disagreement was reachable: a build crediting `ΣΣ` a
second time crashed. These tests pin the replacement — a column default that no insert path can
skip, plus a check constraint for the paths that bypass the ORM entirely.
"""

# ruff: noqa: RUF001  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

import pytest
from sqlalchemy import Table, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import fold_creator_name
from squid.accounts.infrastructure.models import CreatorAlias
from squid.accounts.infrastructure.repository import AccountRepository


@pytest.fixture
def repository(migrated_session_factory: async_sessionmaker[AsyncSession]) -> AccountRepository:
    return AccountRepository(migrated_session_factory, "pepper")


async def _stored_fold(session_factory: async_sessionmaker[AsyncSession], name: str) -> str | None:
    async with session_factory() as session:
        return await session.scalar(select(CreatorAlias.normalized_name).where(CreatorAlias.name == name))


async def test_orm_insert_writes_the_fold(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        session.add(CreatorAlias(name="  ΣΣ  "))

    assert await _stored_fold(migrated_session_factory, "  ΣΣ  ") == "σσ"


async def test_core_on_conflict_insert_writes_the_fold(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The build submission path inserts this way, not through the ORM unit of work."""
    async with migrated_session_factory.begin() as session:
        await session.execute(
            pg_insert(CreatorAlias)
            .values(name="Ａlice")
            .on_conflict_do_nothing(index_elements=[CreatorAlias.normalized_name])
        )

    assert await _stored_fold(migrated_session_factory, "Ａlice") == "alice"


async def test_executemany_writes_the_fold(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alias_table = CreatorAlias.__table__
    assert isinstance(alias_table, Table)  # Declarative only promises the wider `FromClause`.
    async with migrated_session_factory.begin() as session:
        await session.execute(alias_table.insert(), [{"name": "ﬁx"}, {"name": " Bob "}])

    assert await _stored_fold(migrated_session_factory, "ﬁx") == "fix"
    assert await _stored_fold(migrated_session_factory, " Bob ") == "bob"


async def test_updating_the_name_refolds(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`_refold_on_name_change` keeps the fold in step if a display spelling is corrected.

    A column-level `onupdate` cannot do this job: it would also fire for the claim updates,
    which never mention `name` at all.
    """
    async with migrated_session_factory.begin() as session:
        session.add(CreatorAlias(name="Bob"))
    async with migrated_session_factory.begin() as session:
        alias = await session.scalar(select(CreatorAlias).where(CreatorAlias.name == "Bob"))
        assert alias is not None
        alias.name = "Straße"

    assert await _stored_fold(migrated_session_factory, "Straße") == "strasse"


async def test_check_constraint_rejects_an_unfolded_raw_write(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The one guard left for writers that bypass SQLAlchemy's default entirely."""
    async with migrated_session_factory() as session:
        with pytest.raises(IntegrityError, match="creator_aliases_normalized_name_folded"):
            await session.execute(text("INSERT INTO creator_aliases (name, normalized_name) VALUES ('Bob', 'Bob')"))


async def test_names_that_only_casefold_unifies_are_one_creator(
    repository: AccountRepository,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`lower(btrim(...))` kept these apart; casefold does not, and the index now agrees."""
    async with migrated_session_factory.begin() as session:
        session.add(CreatorAlias(name="ΣΣ"))

    async with migrated_session_factory() as session:
        session.add(CreatorAlias(name="Σς"))
        with pytest.raises(IntegrityError):
            await session.commit()

    # And the alias is reachable by either spelling, which it was not before.
    for spelling in ("ΣΣ", "Σς", "σσ"):
        alias = await repository.get_alias_by_name(spelling)
        assert alias is not None, f"{spelling!r} did not resolve"
        assert alias.name == "ΣΣ"


async def test_dotted_capital_i_stays_a_distinct_creator(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Postgres `lower()` folded `İ` onto `i`; the application deliberately does not."""
    async with migrated_session_factory.begin() as session:
        session.add_all([CreatorAlias(name="İ"), CreatorAlias(name="I")])

    async with migrated_session_factory() as session:
        folds = set((await session.scalars(select(CreatorAlias.normalized_name))).all())
        assert folds == {fold_creator_name("İ"), "i"}
