"""Account and tab isolation for saved catalogue choices."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account, CataloguePreference
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_catalogue_preferences_replace_and_clear_one_tab(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        first, second = Account(), Account()
        session.add_all([first, second])
        await session.flush()
        first_id, second_id = first.id, second.id

    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test")
    for account_id, scope, filters, sort in (
        (first_id, "builds", "width:3", "width"),
        (second_id, "builds", "", "-width"),
        (first_id, "records", "base_title:fastest", ""),
        (first_id, "builds", "width:5", "-width"),
    ):
        async with factory() as uow:
            await uow.accounts.save_catalogue_preference(
                CataloguePreference(account_id=account_id, scope=scope, filters=filters, sort=sort)
            )
            await uow.commit()
    async with factory() as uow:
        rows = await uow.accounts.catalogue_preferences(first_id)
        assert {(row.scope, row.filters, row.sort) for row in rows} == {
            ("builds", "width:5", "-width"),
            ("records", "base_title:fastest", ""),
        }
        await uow.accounts.save_catalogue_preference(
            CataloguePreference(account_id=first_id, scope="builds", filters="", sort="")
        )
        await uow.commit()
    async with factory() as uow:
        assert [row.scope for row in await uow.accounts.catalogue_preferences(first_id)] == ["records"]
        assert [row.sort for row in await uow.accounts.catalogue_preferences(second_id)] == ["-width"]
