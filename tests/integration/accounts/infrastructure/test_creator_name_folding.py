"""Creator aliases use Python normalization consistently across SQLAlchemy insert paths.

Unicode case folding differs from SQL lower(); the SQLAlchemy default supplies the folded name,
while a database CHECK rejects visibly unfolded raw writes.
"""

# ruff: noqa: RUF001  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

from functools import partial

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import fold_creator_name
from squid.accounts.models import CreatorAlias
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.schema import model_table
from tests.support.unit_of_work import transact


@pytest.fixture
def repository(migrated_session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="pepper")


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
    alias_table = model_table(CreatorAlias)
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
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Greek sigma variants share a casefolded key and therefore collide at the unique index."""
    async with migrated_session_factory.begin() as session:
        session.add(CreatorAlias(name="ΣΣ"))

    async with migrated_session_factory() as session:
        session.add(CreatorAlias(name="Σς"))
        with pytest.raises(IntegrityError):
            await session.commit()

    # Lookup must use the same folding as insertion and uniqueness checks.
    for spelling in ("ΣΣ", "Σς", "σσ"):
        alias = await transact(repository, lambda uow, spelling=spelling: uow.accounts.get_alias_by_name(spelling))
        assert alias is not None, f"{spelling!r} did not resolve"
        assert alias.name == "ΣΣ"


async def test_dotted_capital_i_stays_a_distinct_creator(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Case folding preserves the combining dot in capital dotted I, keeping it distinct from plain I."""
    async with migrated_session_factory.begin() as session:
        session.add_all([CreatorAlias(name="İ"), CreatorAlias(name="I")])

    async with migrated_session_factory() as session:
        folds = set((await session.scalars(select(CreatorAlias.normalized_name))).all())
        assert folds == {fold_creator_name("İ"), "i"}
