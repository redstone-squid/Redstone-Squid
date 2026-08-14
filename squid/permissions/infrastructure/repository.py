"""PostgreSQL persistence for permissions."""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.permissions.application.ports import (
    AssignmentRecord,
    AssignmentRow,
    AuditEntry,
    AuditRow,
    GrantRecord,
    RoleRecord,
    RuleRow,
    SubjectRecords,
)
from squid.permissions.domain import GlobalAdministrator
from squid.permissions.infrastructure.models import (
    GlobalAdministrator as GlobalAdministratorModel,
)
from squid.permissions.infrastructure.models import (
    PermissionAuditEntry,
    PermissionEpoch,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRoleInclude,
    PermissionRolePattern,
)

INCLUDE_MODE = 1
EXCLUDE_MODE = -1

EPOCH_CHANNEL = "squid_permissions"
"""The channel `bump_permission_epoch()` notifies after any permission write."""


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


class PermissionAdminRepository:
    """Write permissions, appending the audit row in the same transaction.

    An audit trail that can be half-written is not an audit trail, so every
    mutation below commits with its own entry or not at all.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    @staticmethod
    def _audit(entry: AuditEntry) -> PermissionAuditEntry:
        return PermissionAuditEntry(
            action=entry.action,
            actor_account_id=entry.actor_account_id,
            subject_kind=entry.subject_kind,
            subject_id=entry.subject_id,
            subject_guild_id=entry.subject_guild_id,
            pattern=entry.pattern,
            role_id=entry.role_id,
            scope_guild_id=entry.scope_guild_id,
            effect=entry.effect,
            expires_at=entry.expires_at,
            reason=entry.reason,
            details=entry.details,
        )

    async def upsert_grant(
        self,
        *,
        pattern: str,
        effect: int,
        subject_account_id: int | None,
        subject_role_id: int | None,
        subject_guild_id: int | None,
        scope_guild_id: int | None,
        expires_at: Instant | None,
        granted_by_account_id: int | None,
        reason: str | None,
        audit: AuditEntry,
    ) -> None:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(PermissionGrant).where(
                    PermissionGrant.pattern == pattern,
                    PermissionGrant.subject_account_id.is_not_distinct_from(subject_account_id),
                    PermissionGrant.subject_role_id.is_not_distinct_from(subject_role_id),
                    PermissionGrant.scope_guild_id.is_not_distinct_from(scope_guild_id),
                )
            )
            if existing is None:
                session.add(
                    PermissionGrant(
                        pattern=pattern,
                        effect=effect,
                        subject_account_id=subject_account_id,
                        subject_role_id=subject_role_id,
                        subject_guild_id=subject_guild_id,
                        scope_guild_id=scope_guild_id,
                        expires_at=expires_at,
                        granted_by_account_id=granted_by_account_id,
                        reason=reason,
                    )
                )
            else:
                existing.effect = effect
                existing.expires_at = expires_at
                existing.granted_by_account_id = granted_by_account_id
                existing.reason = reason
            session.add(self._audit(audit))
            await session.commit()

    async def delete_grant(
        self,
        *,
        pattern: str,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PermissionGrant)
                .where(
                    PermissionGrant.pattern == pattern,
                    PermissionGrant.subject_account_id.is_not_distinct_from(subject_account_id),
                    PermissionGrant.subject_role_id.is_not_distinct_from(subject_role_id),
                    PermissionGrant.scope_guild_id.is_not_distinct_from(scope_guild_id),
                )
                .returning(PermissionGrant.id)
            )
            removed = result.scalar_one_or_none() is not None
            if removed:
                session.add(self._audit(audit))
            await session.commit()
            return removed

    async def list_rules(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[RuleRow, ...]:
        statement = select(PermissionGrant)
        if subject_account_id is not None:
            statement = statement.where(PermissionGrant.subject_account_id == subject_account_id)
        if subject_role_id is not None:
            statement = statement.where(PermissionGrant.subject_role_id == subject_role_id)
        if guild_id is not None:
            statement = statement.where(
                or_(
                    PermissionGrant.subject_guild_id == guild_id,
                    PermissionGrant.scope_guild_id == guild_id,
                )
            )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement.order_by(PermissionGrant.id))).all()
        return tuple(
            RuleRow(
                id=row.id,
                pattern=row.pattern,
                effect=row.effect,
                subject_account_id=row.subject_account_id,
                subject_role_id=row.subject_role_id,
                subject_guild_id=row.subject_guild_id,
                scope_guild_id=row.scope_guild_id,
                expires_at=row.expires_at,
                granted_by_account_id=row.granted_by_account_id,
                granted_at=row.granted_at,
                reason=row.reason,
            )
            for row in rows
        )

    async def list_assignments(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[AssignmentRow, ...]:
        statement = select(PermissionRoleAssignment, PermissionRole.slug).join(
            PermissionRole, PermissionRole.id == PermissionRoleAssignment.role_id
        )
        if subject_account_id is not None:
            statement = statement.where(PermissionRoleAssignment.subject_account_id == subject_account_id)
        if subject_role_id is not None:
            statement = statement.where(PermissionRoleAssignment.subject_role_id == subject_role_id)
        if guild_id is not None:
            statement = statement.where(
                or_(
                    PermissionRoleAssignment.subject_guild_id == guild_id,
                    PermissionRoleAssignment.scope_guild_id == guild_id,
                    PermissionRoleAssignment.subject_account_id.is_not(None),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement.order_by(PermissionRoleAssignment.id))).all()
        return tuple(
            AssignmentRow(
                id=assignment.id,
                role_id=assignment.role_id,
                role_slug=slug,
                subject_account_id=assignment.subject_account_id,
                subject_role_id=assignment.subject_role_id,
                subject_guild_id=assignment.subject_guild_id,
                scope_guild_id=assignment.scope_guild_id,
                expires_at=assignment.expires_at,
            )
            for assignment, slug in rows
        )

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[RoleRecord, ...]:
        statement = select(PermissionRole)
        if guild_id is not None:
            statement = statement.where(or_(PermissionRole.guild_id.is_(None), PermissionRole.guild_id == guild_id))
        async with self._session_factory() as session:
            roles = (await session.scalars(statement)).all()
            patterns = (await session.scalars(select(PermissionRolePattern))).all()
            includes = (await session.scalars(select(PermissionRoleInclude))).all()
        return _role_records(roles, patterns, includes)

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> RoleRecord | None:
        # A guild's own role shadows a global one of the same slug, which is what
        # makes `/role create moderator` usable in more than one server.
        candidates = [role for role in await self.list_roles(guild_id=guild_id) if role.slug == slug]
        candidates.sort(key=lambda role: role.guild_id is None)
        return candidates[0] if candidates else None

    async def create_role(
        self,
        *,
        slug: str,
        name: str,
        description: str | None,
        guild_id: int | None,
        rank: int,
        created_by_account_id: int | None,
        audit: AuditEntry,
    ) -> RoleRecord:
        async with self._session_factory() as session:
            role = PermissionRole(
                slug=slug,
                name=name,
                description=description,
                guild_id=guild_id,
                rank=rank,
                created_by_account_id=created_by_account_id,
            )
            session.add(role)
            await session.flush()
            session.add(self._audit(replace(audit, role_id=role.id)))
            await session.commit()
            return RoleRecord(
                id=role.id,
                slug=role.slug,
                guild_id=role.guild_id,
                builtin_key=role.builtin_key,
                rank=role.rank,
                protected=role.protected,
            )

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PermissionRole).where(PermissionRole.id == role_id).returning(PermissionRole.id)
            )
            removed = result.scalar_one_or_none() is not None
            if removed:
                session.add(self._audit(audit))
            await session.commit()
            return removed

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(update(PermissionRole).where(PermissionRole.id == role_id).values(rank=rank))
            session.add(self._audit(audit))
            await session.commit()

    async def set_role_pattern(
        self,
        role_id: int,
        pattern: str,
        mode: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        async with self._session_factory() as session:
            # One mode per pattern per role, so a role can never contradict
            # itself: re-adding with the other mode replaces rather than stacks.
            await session.execute(
                insert(PermissionRolePattern)
                .values(role_id=role_id, pattern=pattern, mode=mode, added_by_account_id=added_by_account_id)
                .on_conflict_do_update(index_elements=["role_id", "pattern"], set_={"mode": mode})
            )
            session.add(self._audit(audit))
            await session.commit()

    async def remove_role_pattern(self, role_id: int, pattern: str, *, audit: AuditEntry) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PermissionRolePattern)
                .where(PermissionRolePattern.role_id == role_id, PermissionRolePattern.pattern == pattern)
                .returning(PermissionRolePattern.pattern)
            )
            removed = result.scalar_one_or_none() is not None
            if removed:
                session.add(self._audit(audit))
            await session.commit()
            return removed

    async def add_role_include(
        self,
        role_id: int,
        included_role_id: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                insert(PermissionRoleInclude)
                .values(role_id=role_id, included_role_id=included_role_id, added_by_account_id=added_by_account_id)
                .on_conflict_do_nothing(index_elements=["role_id", "included_role_id"])
            )
            session.add(self._audit(audit))
            await session.commit()

    async def remove_role_include(self, role_id: int, included_role_id: int, *, audit: AuditEntry) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PermissionRoleInclude)
                .where(
                    PermissionRoleInclude.role_id == role_id,
                    PermissionRoleInclude.included_role_id == included_role_id,
                )
                .returning(PermissionRoleInclude.role_id)
            )
            removed = result.scalar_one_or_none() is not None
            if removed:
                session.add(self._audit(audit))
            await session.commit()
            return removed

    async def assign_role(
        self,
        role_id: int,
        *,
        subject_account_id: int | None,
        subject_role_id: int | None,
        subject_guild_id: int | None,
        scope_guild_id: int | None,
        expires_at: Instant | None,
        granted_by_account_id: int | None,
        reason: str | None,
        audit: AuditEntry,
    ) -> None:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(PermissionRoleAssignment).where(
                    PermissionRoleAssignment.role_id == role_id,
                    PermissionRoleAssignment.subject_account_id.is_not_distinct_from(subject_account_id),
                    PermissionRoleAssignment.subject_role_id.is_not_distinct_from(subject_role_id),
                    PermissionRoleAssignment.scope_guild_id.is_not_distinct_from(scope_guild_id),
                )
            )
            if existing is None:
                session.add(
                    PermissionRoleAssignment(
                        role_id=role_id,
                        subject_account_id=subject_account_id,
                        subject_role_id=subject_role_id,
                        subject_guild_id=subject_guild_id,
                        scope_guild_id=scope_guild_id,
                        expires_at=expires_at,
                        granted_by_account_id=granted_by_account_id,
                        reason=reason,
                    )
                )
            else:
                existing.expires_at = expires_at
                existing.granted_by_account_id = granted_by_account_id
                existing.reason = reason
            session.add(self._audit(audit))
            await session.commit()

    async def unassign_role(
        self,
        role_id: int,
        *,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(PermissionRoleAssignment)
                .where(
                    PermissionRoleAssignment.role_id == role_id,
                    PermissionRoleAssignment.subject_account_id.is_not_distinct_from(subject_account_id),
                    PermissionRoleAssignment.subject_role_id.is_not_distinct_from(subject_role_id),
                    PermissionRoleAssignment.scope_guild_id.is_not_distinct_from(scope_guild_id),
                )
                .returning(PermissionRoleAssignment.id)
            )
            removed = result.scalar_one_or_none() is not None
            if removed:
                session.add(self._audit(audit))
            await session.commit()
            return removed

    async def list_audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[AuditRow, ...]:
        statement = select(PermissionAuditEntry).order_by(PermissionAuditEntry.at.desc()).limit(limit)
        if guild_id is not None:
            statement = statement.where(
                or_(
                    PermissionAuditEntry.subject_guild_id == guild_id,
                    PermissionAuditEntry.scope_guild_id == guild_id,
                )
            )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(
            AuditRow(
                id=row.id,
                action=row.action,
                at=row.at,
                actor_account_id=row.actor_account_id,
                subject_kind=row.subject_kind,
                subject_id=row.subject_id,
                subject_guild_id=row.subject_guild_id,
                pattern=row.pattern,
                role_id=row.role_id,
                scope_guild_id=row.scope_guild_id,
                effect=row.effect,
                reason=row.reason,
            )
            for row in rows
        )


def _role_records(
    roles: Sequence[PermissionRole],
    patterns: Sequence[PermissionRolePattern],
    includes: Sequence[PermissionRoleInclude],
) -> tuple[RoleRecord, ...]:
    """Fold pattern and composition rows into the role records they belong to."""
    by_includes: dict[int, list[str]] = {}
    by_excludes: dict[int, list[str]] = {}
    for pattern in patterns:
        target = by_includes if pattern.mode == INCLUDE_MODE else by_excludes
        target.setdefault(pattern.role_id, []).append(pattern.pattern)

    by_edges: dict[int, list[int]] = {}
    for edge in includes:
        by_edges.setdefault(edge.role_id, []).append(edge.included_role_id)

    return tuple(
        RoleRecord(
            id=role.id,
            slug=role.slug,
            guild_id=role.guild_id,
            builtin_key=role.builtin_key,
            rank=role.rank,
            protected=role.protected,
            includes=tuple(sorted(by_includes.get(role.id, ()))),
            excludes=tuple(sorted(by_excludes.get(role.id, ()))),
            includes_roles=tuple(sorted(by_edges.get(role.id, ()))),
        )
        for role in roles
    )
