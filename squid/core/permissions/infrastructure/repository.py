"""PostgreSQL persistence for permissions."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import Select, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import aggregate_order_by, insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.core.actors import UserActor
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.application.ports import AuditEntry, PermissionSubject, SubjectRecords
from squid.core.permissions.domain.models import PatternMode
from squid.core.permissions.models import (
    PermissionAuditEntry,
    PermissionEpoch,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
    PermissionRoleInclude,
    PermissionRolePattern,
)
from squid.core.persistence.paging import KeysetOrder

_ROLE_PATTERNS = (
    select(
        PermissionRolePattern.role_id,
        func.array_agg(aggregate_order_by(PermissionRolePattern.pattern, PermissionRolePattern.pattern))
        .filter(PermissionRolePattern.mode == PatternMode.INCLUDE)
        .label("includes"),
        func.array_agg(aggregate_order_by(PermissionRolePattern.pattern, PermissionRolePattern.pattern))
        .filter(PermissionRolePattern.mode == PatternMode.EXCLUDE)
        .label("excludes"),
    )
    .group_by(PermissionRolePattern.role_id)
    .subquery()
)
_ROLE_EDGES = (
    select(
        PermissionRoleInclude.role_id,
        func.array_agg(
            aggregate_order_by(PermissionRoleInclude.included_role_id, PermissionRoleInclude.included_role_id)
        ).label("edges"),
    )
    .group_by(PermissionRoleInclude.role_id)
    .subquery()
)
_ROLE_DEFINITIONS = (
    select(PermissionRole, _ROLE_PATTERNS.c.includes, _ROLE_PATTERNS.c.excludes, _ROLE_EDGES.c.edges)
    .outerjoin(_ROLE_PATTERNS, PermissionRole.id == _ROLE_PATTERNS.c.role_id)
    .outerjoin(_ROLE_EDGES, PermissionRole.id == _ROLE_EDGES.c.role_id)
    .where(PermissionRole.withdrawn.is_(False))
    .order_by(PermissionRole.id)
)

EPOCH_CHANNEL = "squid_permissions"
"""The channel `bump_permission_epoch()` notifies after any permission write."""


class PostgresPermissionRepository:
    """Load permission rule sources and the epoch they were read at."""

    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    async def epoch(self) -> int:
        session = self._session()
        version = await session.scalar(select(PermissionEpoch.version).where(PermissionEpoch.id == 1))
        return version or 0

    async def load_for_subject(self, subject: PermissionSubject) -> SubjectRecords:
        """Load the epoch, all role definitions, and grants and assignments for one subject.

        Loading all roles allows composition to be resolved without recursive database
        queries. Expiry and permission scope are checked by the resolver, not this query.
        """
        account_id = subject.account_id if isinstance(subject, UserActor) else None
        guild_id = subject.guild_id
        role_ids = tuple(sorted(subject.discord_role_ids))

        session = self._session()
        # Read the epoch first so a concurrent write leaves an older stamp.
        # The cache discards these rules once it observes the newer epoch.
        epoch = await session.scalar(select(PermissionEpoch.version).where(PermissionEpoch.id == 1)) or 0

        roles = await self._load_roles(session)
        grants = await self._load_grants(session, account_id, role_ids, guild_id)
        assignments = await self._load_assignments(session, account_id, role_ids, guild_id)

        return SubjectRecords(subject=subject, epoch=epoch, roles=roles, grants=grants, assignments=assignments)

    async def _load_roles(self, session: AsyncSession) -> tuple[PermissionRole, ...]:
        return _role_records((await session.execute(_ROLE_DEFINITIONS)).all())

    async def _load_grants(
        self,
        session: AsyncSession,
        account_id: int | None,
        role_ids: tuple[int, ...],
        guild_id: int | None,
    ) -> tuple[PermissionGrant, ...]:
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
        return tuple(row for row in rows)

    async def _load_assignments(
        self,
        session: AsyncSession,
        account_id: int | None,
        role_ids: tuple[int, ...],
        guild_id: int | None,
    ) -> tuple[PermissionRoleAssignment, ...]:
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
        return tuple(row for row in rows)


class PostgresPermissionAdminRepository:
    """Write permissions and audit entries in the caller's transaction.

    Methods flush changes but leave commit and rollback to the unit of work.
    """

    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    @staticmethod
    def _audit(entry: AuditEntry) -> PermissionAuditEntry:
        return PermissionAuditEntry(
            action=entry.action.value,
            actor_account_id=entry.actor_account_id,
            subject_kind=None if entry.subject_kind is None else entry.subject_kind.value,
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

    async def lock_graph(self) -> int:
        version = await self._session().scalar(
            select(PermissionEpoch.version).where(PermissionEpoch.id == 1).with_for_update()
        )
        assert version is not None
        return version

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
        session = self._session()
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
        await session.flush()

    async def delete_grant(
        self,
        *,
        pattern: str,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        session = self._session()
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
        await session.flush()
        return removed

    async def list_rules(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[PermissionGrant, ...]:
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
        session = self._session()
        rows = (await session.scalars(statement.order_by(PermissionGrant.id))).all()
        return tuple(row for row in rows)

    async def list_assignments(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[PermissionRoleAssignment, ...]:
        """List assignments, optionally filtering subjects and guild-related rows.

        The guild filter retains every account assignment, including those scoped
        to another guild. Callers computing effective rank must check scope and expiry.
        """
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
        session = self._session()
        rows = (await session.execute(statement.order_by(PermissionRoleAssignment.id))).all()
        for assignment, slug in rows:
            assignment.role_slug = slug
        return tuple(assignment for assignment, _ in rows)

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[PermissionRole, ...]:
        statement = _ROLE_DEFINITIONS
        if guild_id is not None:
            statement = statement.where(or_(PermissionRole.guild_id.is_(None), PermissionRole.guild_id == guild_id))
        session = self._session()
        return _role_records((await session.execute(statement)).all())

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> PermissionRole | None:
        # A guild's own role shadows a global one of the same slug, which is what
        # makes `/role create moderator` usable in more than one server.
        candidates = [role for role in await self.list_roles(guild_id=guild_id) if role.slug == slug]
        candidates.sort(key=lambda role: role.guild_id is None)
        return candidates[0] if candidates else None

    async def reserve_role_identity(self) -> int:
        identity = await self._session().scalar(
            text("SELECT nextval(pg_get_serial_sequence('permission_roles', 'id'))")
        )
        assert isinstance(identity, int)
        return identity

    async def role_identity(self, role_id: int) -> PermissionRole | None:
        return await self._session().get(PermissionRole, role_id)

    async def unused_role(self, role_id: int) -> bool:
        used = await self._session().scalar(
            select(
                or_(
                    exists().where(PermissionRolePattern.role_id == role_id),
                    exists().where(
                        or_(PermissionRoleInclude.role_id == role_id, PermissionRoleInclude.included_role_id == role_id)
                    ),
                    exists().where(PermissionRoleAssignment.role_id == role_id),
                )
            )
        )
        return not used

    async def role_slug_available(self, slug: str, guild_id: int | None, role_id: int) -> bool:
        return (
            await self._session().scalar(
                select(PermissionRole.id).where(
                    PermissionRole.slug == slug,
                    PermissionRole.guild_id.is_not_distinct_from(guild_id),
                    PermissionRole.id != role_id,
                )
            )
            is None
        )

    async def save_role_presence(self, role: PermissionRole, *, audit: AuditEntry) -> None:
        session = self._session()
        session.add(role)
        session.add(self._audit(audit))
        await session.flush()

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
    ) -> PermissionRole:
        session = self._session()
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
        await session.flush()
        return role

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool:
        session = self._session()
        result = await session.execute(
            delete(PermissionRole).where(PermissionRole.id == role_id).returning(PermissionRole.id)
        )
        removed = result.scalar_one_or_none() is not None
        if removed:
            session.add(self._audit(audit))
        await session.flush()
        return removed

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None:
        session = self._session()
        await session.execute(update(PermissionRole).where(PermissionRole.id == role_id).values(rank=rank))
        session.add(self._audit(audit))
        await session.flush()

    async def set_role_pattern(
        self,
        role_id: int,
        pattern: str,
        mode: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        session = self._session()
        # One mode per pattern per role, so a role can never contradict
        # itself: re-adding with the other mode replaces rather than stacks.
        await session.execute(
            insert(PermissionRolePattern)
            .values(role_id=role_id, pattern=pattern, mode=mode, added_by_account_id=added_by_account_id)
            .on_conflict_do_update(index_elements=["role_id", "pattern"], set_={"mode": mode})
        )
        session.add(self._audit(audit))
        await session.flush()

    async def remove_role_pattern(self, role_id: int, pattern: str, *, audit: AuditEntry) -> bool:
        session = self._session()
        result = await session.execute(
            delete(PermissionRolePattern)
            .where(PermissionRolePattern.role_id == role_id, PermissionRolePattern.pattern == pattern)
            .returning(PermissionRolePattern.pattern)
        )
        removed = result.scalar_one_or_none() is not None
        if removed:
            session.add(self._audit(audit))
        await session.flush()
        return removed

    async def add_role_include(
        self,
        role_id: int,
        included_role_id: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        session = self._session()
        await session.execute(
            insert(PermissionRoleInclude)
            .values(role_id=role_id, included_role_id=included_role_id, added_by_account_id=added_by_account_id)
            .on_conflict_do_nothing(index_elements=["role_id", "included_role_id"])
        )
        session.add(self._audit(audit))
        await session.flush()

    async def remove_role_include(self, role_id: int, included_role_id: int, *, audit: AuditEntry) -> bool:
        session = self._session()
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
        await session.flush()
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
        session = self._session()
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
        await session.flush()

    async def unassign_role(
        self,
        role_id: int,
        *,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        session = self._session()
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
        await session.flush()
        return removed

    async def list_audit(
        self, *, guild_id: int | None = None, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[PermissionAuditEntry]:
        session = self._session()
        order = KeysetOrder("newest", PermissionAuditEntry.at, PermissionAuditEntry.id, descending=True)
        total = await session.scalar(_audit_in(select(func.count()).select_from(PermissionAuditEntry), guild_id))
        return await order.page(
            session, _audit_in(select(PermissionAuditEntry), guild_id), selector, page_size=page_size, total=total or 0
        )


def _audit_in[S: Select[Any]](statement: S, guild_id: int | None) -> S:
    """Keep entries about `guild_id`, or every entry when it is None."""
    if guild_id is None:
        return statement
    return statement.where(
        or_(PermissionAuditEntry.subject_guild_id == guild_id, PermissionAuditEntry.scope_guild_id == guild_id)
    )


def _role_records(
    rows: Sequence[Row[PermissionRole, list[str] | None, list[str] | None, list[int] | None]],
) -> tuple[PermissionRole, ...]:
    """Attach separately aggregated patterns and edges to each loaded role."""
    roles: list[PermissionRole] = []
    for role, includes, excludes, edges in rows:
        role.includes = tuple(includes or ())
        role.excludes = tuple(excludes or ())
        role.includes_roles = tuple(edges or ())
        roles.append(role)
    return tuple(roles)
