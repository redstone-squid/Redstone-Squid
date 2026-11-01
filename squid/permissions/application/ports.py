"""Application ports for permissions."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from whenever import Instant

from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment


@dataclass(frozen=True, slots=True)
class SubjectRecords:
    """Everything one subject's decisions depend on, loaded in a single call.

    Carries the epoch it was read at so a cache entry can be discarded the moment
    any process writes a permission, rather than aged out on a timer.
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
    """Resolves an actor's node names for contexts that must not depend on us.

    The voting and reactions contexts hold their authorization as a set of
    already-resolved capability names, so they never import the permissions
    application package and stay framework-independent.
    """

    async def capabilities_for(
        self,
        *,
        account_id: int | None,
        discord_role_ids: Iterable[int],
        guild_id: int | None,
        is_bot_owner: bool = False,
        discord_guild_admin: bool = False,
        nodes: Iterable[str] = (),
    ) -> frozenset[str]:
        """The subset of `nodes` the described actor holds."""
        ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """What the service asks the store to append when it changes something."""

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
    """Write operations behind `/perm` and `/role`.

    Every mutation takes the audit entry that describes it, because the log is
    written in the same transaction as the change: an audit trail that can be
    half-written is not an audit trail.
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
