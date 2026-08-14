"""PostgreSQL persistence for permissions."""

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.permissions.application.ports import AssignmentRecord, GrantRecord, RoleRecord, SubjectRecords
from squid.permissions.domain import GlobalAdministrator
from squid.permissions.infrastructure.models import (
    GlobalAdministrator as GlobalAdministratorModel,
)
from squid.permissions.infrastructure.models import (
    PermissionEpoch,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRoleInclude,
    PermissionRolePattern,
)

INCLUDE_MODE = 1
EXCLUDE_MODE = -1


def _to_domain(model: GlobalAdministratorModel) -> GlobalAdministrator:
    return GlobalAdministrator(
        account_id=model.account_id,
        granted_by_account_id=model.granted_by_account_id,
        granted_at=model.granted_at,
    )


class GlobalAdministratorRepository:
    """Persist active global administrator grants."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def contains(self, account_id: int) -> bool:
        async with self._session_factory() as session:
            return (
                await session.scalar(
                    select(GlobalAdministratorModel.account_id).where(GlobalAdministratorModel.account_id == account_id)
                )
                is not None
            )

    async def list(self) -> Sequence[GlobalAdministrator]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(GlobalAdministratorModel).order_by(
                        GlobalAdministratorModel.granted_at, GlobalAdministratorModel.account_id
                    )
                )
            ).all()
            return tuple(_to_domain(row) for row in rows)

    async def grant(self, account_id: int, granted_by_account_id: int) -> GlobalAdministrator:
        async with self._session_factory() as session:
            statement = (
                insert(GlobalAdministratorModel)
                .values(account_id=account_id, granted_by_account_id=granted_by_account_id)
                .on_conflict_do_nothing(index_elements=[GlobalAdministratorModel.account_id])
                .returning(GlobalAdministratorModel)
            )
            row = await session.scalar(statement)
            if row is None:
                row = await session.scalar(
                    select(GlobalAdministratorModel).where(GlobalAdministratorModel.account_id == account_id)
                )
            assert row is not None
            await session.commit()
            return _to_domain(row)

    async def revoke(self, account_id: int) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(GlobalAdministratorModel)
                .where(GlobalAdministratorModel.account_id == account_id)
                .returning(GlobalAdministratorModel.account_id)
            )
            removed = result.scalar_one_or_none() is not None
            await session.commit()
            return removed


class PermissionRepository:
    """Load permission rule sources and the epoch they were read at."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def epoch(self) -> int:
        async with self._session_factory() as session:
            version = await session.scalar(select(PermissionEpoch.version).where(PermissionEpoch.id == 1))
            return version or 0

    async def load_for_subject(
        self,
        *,
        account_id: int | None,
        discord_role_ids: Iterable[int],
        guild_id: int | None,
    ) -> SubjectRecords:
        """Load everything one subject's decisions depend on, in one session.

        Every role is loaded rather than only the assigned ones. Roles compose,
        so following the graph selectively would mean a recursive query or a
        round trip per level; the table is small, and one flat read keeps the
        promise that resolving N nodes costs one call.
        """
        role_ids = tuple(dict.fromkeys(discord_role_ids))

        async with self._session_factory() as session:
            # Read the epoch first: if a write lands mid-read, the cache entry is
            # stamped with the older epoch and gets discarded rather than served.
            epoch = await session.scalar(select(PermissionEpoch.version).where(PermissionEpoch.id == 1)) or 0

            roles = await self._load_roles(session)
            grants = await self._load_grants(session, account_id, role_ids, guild_id)
            assignments = await self._load_assignments(session, account_id, role_ids, guild_id)

        return SubjectRecords(epoch=epoch, roles=roles, grants=grants, assignments=assignments)

    async def _load_roles(self, session: AsyncSession) -> tuple[RoleRecord, ...]:
        rows = (await session.scalars(select(PermissionRole))).all()
        patterns = (await session.scalars(select(PermissionRolePattern))).all()
        includes = (await session.scalars(select(PermissionRoleInclude))).all()

        by_role_includes: dict[int, list[str]] = {}
        by_role_excludes: dict[int, list[str]] = {}
        for pattern in patterns:
            target = by_role_includes if pattern.mode == INCLUDE_MODE else by_role_excludes
            target.setdefault(pattern.role_id, []).append(pattern.pattern)

        by_role_edges: dict[int, list[int]] = {}
        for edge in includes:
            by_role_edges.setdefault(edge.role_id, []).append(edge.included_role_id)

        return tuple(
            RoleRecord(
                id=row.id,
                slug=row.slug,
                guild_id=row.guild_id,
                builtin_key=row.builtin_key,
                rank=row.rank,
                protected=row.protected,
                includes=tuple(sorted(by_role_includes.get(row.id, ()))),
                excludes=tuple(sorted(by_role_excludes.get(row.id, ()))),
                includes_roles=tuple(sorted(by_role_edges.get(row.id, ()))),
            )
            for row in rows
        )

    async def _load_grants(
        self,
        session: AsyncSession,
        account_id: int | None,
        role_ids: tuple[int, ...],
        guild_id: int | None,
    ) -> tuple[GrantRecord, ...]:
        conditions = []
        if account_id is not None:
            conditions.append(PermissionGrant.subject_account_id == account_id)
        if role_ids and guild_id is not None:
            conditions.append(
                PermissionGrant.subject_role_id.in_(role_ids) & (PermissionGrant.subject_guild_id == guild_id)
            )
        if not conditions:
            return ()

        rows = (await session.scalars(select(PermissionGrant).where(or_(*conditions)))).all()
        return tuple(
            GrantRecord(
                pattern=row.pattern,
                effect=row.effect,
                subject_account_id=row.subject_account_id,
                subject_role_id=row.subject_role_id,
                subject_guild_id=row.subject_guild_id,
                scope_guild_id=row.scope_guild_id,
                expires_at=row.expires_at,
            )
            for row in rows
        )

    async def _load_assignments(
        self,
        session: AsyncSession,
        account_id: int | None,
        role_ids: tuple[int, ...],
        guild_id: int | None,
    ) -> tuple[AssignmentRecord, ...]:
        conditions = []
        if account_id is not None:
            conditions.append(PermissionRoleAssignment.subject_account_id == account_id)
        if role_ids and guild_id is not None:
            conditions.append(
                PermissionRoleAssignment.subject_role_id.in_(role_ids)
                & (PermissionRoleAssignment.subject_guild_id == guild_id)
            )
        if not conditions:
            return ()

        rows = (await session.scalars(select(PermissionRoleAssignment).where(or_(*conditions)))).all()
        return tuple(
            AssignmentRecord(
                role_id=row.role_id,
                subject_account_id=row.subject_account_id,
                subject_role_id=row.subject_role_id,
                subject_guild_id=row.subject_guild_id,
                scope_guild_id=row.scope_guild_id,
                expires_at=row.expires_at,
            )
            for row in rows
        )
