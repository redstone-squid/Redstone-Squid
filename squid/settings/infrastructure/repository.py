"""SQLAlchemy server settings repository."""

from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.settings.models import ServerSetting


class SettingsRepository:
    """Load settings within the caller's unit of work."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None:
        statement = select(ServerSetting).where(ServerSetting.server_id == server_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session().scalar(statement)

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]:
        return (
            await self._session().scalars(select(ServerSetting).where(ServerSetting.server_id.in_(server_ids)))
        ).all()

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting:
        """Create a missing row safely under concurrent first writes, then lock it."""
        session = self._session()
        await session.execute(insert(ServerSetting).values(server_id=server_id).on_conflict_do_nothing())
        return (
            await session.scalars(select(ServerSetting).where(ServerSetting.server_id == server_id).with_for_update())
        ).one()
