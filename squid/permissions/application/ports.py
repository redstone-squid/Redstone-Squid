"""Application ports for permissions."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from whenever import Instant

from squid.core.actors import Actor
from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment
from squid.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class SubjectRecords:
    """Stored inputs from which PermissionService assembles a subject's rules.

    Grants and assignments belong to the subject. All permission-role definitions
    are included so the service can follow roles that include other roles.
    The epoch is the shared permission-change counter read before these records;
    the cache discards their assembled rules once it observes a newer epoch.
    """

    epoch: int
    roles: tuple[PermissionRole, ...] = ()
    grants: tuple[PermissionGrant, ...] = ()
    assignments: tuple[PermissionRoleAssignment, ...] = ()


class PermissionStore(Protocol):
    """Persistence operations required by :class:`PermissionService`."""

    async def load_for_subject(
        self,
        *,
        account_id: int | None,
        discord_role_ids: Iterable[int],
        guild_id: int | None,
    ) -> SubjectRecords:
        """Load every rule source that could apply to one subject, plus the epoch."""
        ...

    async def epoch(self) -> int:
        """The current permission epoch, bumped by any permission write."""
        ...


class ActorCapabilityResolver(Protocol):
    """Supply resolved permission names when adapters construct voting or reaction actors.

    Their policies receive a set of allowed names rather than a permission service,
    so evaluating a policy does not itself perform permission queries.
    """

    async def capabilities(self, subject: Actor, nodes: Iterable[str]) -> frozenset[str]:
        """Return the subset of requested nodes held by this actor."""
        ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Audit details to append in the same transaction as a permission change."""

    action: str
    actor_account_id: int | None = None
    subject_kind: str | None = None
    subject_id: int | None = None
    subject_guild_id: int | None = None
    pattern: str | None = None
    role_id: int | None = None
    scope_guild_id: int | None = None
    effect: int | None = None
    expires_at: Instant | None = None
    reason: str | None = None
    details: dict[str, object] | None = None


class PermissionAdminStore(Protocol):
    """Permission mutations and administrative queries.

    Each mutation adds its supplied audit entry to the caller's transaction;
    deletions only record an entry when a row was removed.
    """

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
    ) -> None: ...

    async def delete_grant(
        self,
        *,
        pattern: str,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool: ...

    async def list_rules(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[PermissionGrant, ...]: ...

    async def list_assignments(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[PermissionRoleAssignment, ...]: ...

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[PermissionRole, ...]: ...

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> PermissionRole | None: ...

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
    ) -> PermissionRole: ...

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool: ...

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None: ...

    async def set_role_pattern(
        self,
        role_id: int,
        pattern: str,
        mode: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None: ...

    async def remove_role_pattern(self, role_id: int, pattern: str, *, audit: AuditEntry) -> bool: ...

    async def add_role_include(
        self,
        role_id: int,
        included_role_id: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None: ...

    async def remove_role_include(self, role_id: int, included_role_id: int, *, audit: AuditEntry) -> bool: ...

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
    ) -> None: ...

    async def unassign_role(
        self,
        role_id: int,
        *,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool: ...

    async def list_audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[PermissionAuditEntry, ...]: ...


class PermissionUnitOfWork(UnitOfWork, Protocol):
    @property
    def permissions(self) -> PermissionStore: ...


class PermissionAdminUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def permission_admin(self) -> PermissionAdminStore: ...
