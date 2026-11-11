"""SQLAlchemy server settings repository."""

from collections.abc import Callable, Collection, Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.edits.domain import Revision
from squid.persistence.revisions import flush_revision
from squid.settings.domain.runtime import RuntimeSettings
from squid.settings.models import RuntimeSetting, RuntimeSettingChange, ServerSetting


async def ensure_server_settings(session: AsyncSession, server_ids: Iterable[int | None]) -> Sequence[int]:
    """Create default settings rows for guilds that have none, in the caller's transaction.

    `server_settings` is the table of guilds the bot knows about, and several tables reference
    it by foreign key. Its rows are normally created when the bot joins a guild, but that event
    can be missed, so every write that stores a guild ID calls this first instead of assuming the
    row exists. Concurrent callers are safe: the insert skips rows that already exist.

    The new row's `in_server` keeps its default of true. Guild presence is owned by
    `SettingsService.guild_joined`, `guild_removed`, and `sync_presence`, which correct it.

    Args:
        session: The database session of the transaction that will reference the guilds.
        server_ids: Guild IDs to ensure. None, which stands for a DM, is skipped.

    Returns:
        The guild IDs whose rows this call created.
    """
    ids = sorted({server_id for server_id in server_ids if server_id is not None})
    if not ids:
        return ()
    inserted = await session.scalars(
        insert(ServerSetting)
        .values([{"server_id": server_id} for server_id in ids])
        .on_conflict_do_nothing(index_elements=[ServerSetting.server_id])
        .returning(ServerSetting.server_id)
    )
    return inserted.all()


class SettingsRepository:
    """Load settings within the caller's unit of work."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def runtime(self, defaults: RuntimeSettings, *, for_update: bool = False) -> RuntimeSetting:
        session = self._session()
        statement = select(RuntimeSetting).where(RuntimeSetting.id == 1)
        if for_update:
            statement = statement.with_for_update()
        existing = await session.scalar(statement)
        if existing is not None:
            return existing
        # Seed from deployment configuration once; subsequent processes never overwrite it.
        await session.execute(insert(RuntimeSetting).values(id=1, **defaults.model_dump()).on_conflict_do_nothing())
        return (await session.scalars(statement)).one()

    async def flush_runtime_revision(self, row: RuntimeSetting, expected: Revision) -> Revision:
        return await flush_revision(self._session(), row, expected)

    async def record_runtime_change(
        self, actor_account_id: int, before: RuntimeSettings, after: RuntimeSettings
    ) -> None:
        self._session().add(
            RuntimeSettingChange(
                actor_account_id=actor_account_id,
                before=before.model_dump(mode="json"),
                after=after.model_dump(mode="json"),
            )
        )

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
        await ensure_server_settings(session, (server_id,))
        return (
            await session.scalars(select(ServerSetting).where(ServerSetting.server_id == server_id).with_for_update())
        ).one()

    async def sync_presence(self, present_server_ids: Collection[int]) -> tuple[Sequence[int], Sequence[int]]:
        """Make `in_server` true exactly for the given guilds, creating rows for new ones.

        Returns:
            The guilds that became present (including newly created rows) and the guilds
            that became absent.
        """
        session = self._session()
        ids = list(present_server_ids)
        created = set(await ensure_server_settings(session, ids))
        present = ServerSetting.server_id.in_(ids)
        flipped = (
            await session.execute(
                update(ServerSetting)
                .where(ServerSetting.in_server.is_distinct_from(present))
                .values(in_server=present)
                .returning(ServerSetting.server_id, ServerSetting.in_server)
            )
        ).all()
        joined = sorted(created | {server_id for server_id, in_server in flipped if in_server})
        left = sorted(server_id for server_id, in_server in flipped if not in_server)
        return joined, left
