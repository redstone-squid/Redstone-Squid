"""PostgreSQL persistence for bot authorization."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.permissions.domain import GlobalAdministrator
from squid.permissions.infrastructure.models import GlobalAdministrator as GlobalAdministratorModel


def _to_domain(model: GlobalAdministratorModel) -> GlobalAdministrator:
    return GlobalAdministrator(
        discord_id=model.discord_id,
        granted_by_discord_id=model.granted_by_discord_id,
        granted_at=model.granted_at,
    )


class GlobalAdministratorRepository:
    """Persist active global administrator grants."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def contains(self, discord_id: int) -> bool:
        async with self._session_factory() as session:
            return (
                await session.scalar(
                    select(GlobalAdministratorModel.discord_id).where(GlobalAdministratorModel.discord_id == discord_id)
                )
                is not None
            )

    async def list(self) -> Sequence[GlobalAdministrator]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(GlobalAdministratorModel).order_by(
                        GlobalAdministratorModel.granted_at, GlobalAdministratorModel.discord_id
                    )
                )
            ).all()
            return tuple(_to_domain(row) for row in rows)

    async def grant(self, discord_id: int, granted_by_discord_id: int) -> GlobalAdministrator:
        async with self._session_factory() as session:
            statement = (
                insert(GlobalAdministratorModel)
                .values(discord_id=discord_id, granted_by_discord_id=granted_by_discord_id)
                .on_conflict_do_nothing(index_elements=[GlobalAdministratorModel.discord_id])
                .returning(GlobalAdministratorModel)
            )
            row = await session.scalar(statement)
            if row is None:
                row = await session.scalar(
                    select(GlobalAdministratorModel).where(GlobalAdministratorModel.discord_id == discord_id)
                )
            assert row is not None
            await session.commit()
            return _to_domain(row)

    async def revoke(self, discord_id: int) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(GlobalAdministratorModel)
                .where(GlobalAdministratorModel.discord_id == discord_id)
                .returning(GlobalAdministratorModel.discord_id)
            )
            removed = result.scalar_one_or_none() is not None
            await session.commit()
            return removed
