"""Application ports for permissions."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from whenever import Instant


@dataclass(frozen=True, slots=True)
class RoleRecord:
    """A stored role definition, before code-defined built-in patterns are merged in."""

    id: int
    slug: str
    guild_id: int | None
    builtin_key: str | None
    rank: int
    protected: bool
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    includes_roles: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class GrantRecord:
    """A stored direct grant."""

    pattern: str
    effect: int
    subject_account_id: int | None = None
    subject_role_id: int | None = None
    subject_guild_id: int | None = None
    scope_guild_id: int | None = None
    expires_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class AssignmentRecord:
    """A stored role assignment."""

    role_id: int
    subject_account_id: int | None = None
    subject_role_id: int | None = None
    subject_guild_id: int | None = None
    scope_guild_id: int | None = None
    expires_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class SubjectRecords:
    """Everything one subject's decisions depend on, loaded in a single call.

    Carries the epoch it was read at so a cache entry can be discarded the moment
    any process writes a permission, rather than aged out on a timer.
    """

    epoch: int
    roles: tuple[RoleRecord, ...] = ()
    grants: tuple[GrantRecord, ...] = ()
    assignments: tuple[AssignmentRecord, ...] = ()


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

    The voting and reactions contexts hold their authorization as already-resolved capability
    names, so they never import this package.
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
class RuleRow:
    """One stored rule, as `/perm list` and `/perm audit` render it."""

    id: int
    pattern: str
    effect: int
    subject_account_id: int | None = None
    subject_role_id: int | None = None
    subject_guild_id: int | None = None
    scope_guild_id: int | None = None
    expires_at: Instant | None = None
    granted_by_account_id: int | None = None
    granted_at: Instant | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    """One stored role assignment, with the role's slug resolved."""

    id: int
    role_id: int
    role_slug: str
    subject_account_id: int | None = None
    subject_role_id: int | None = None
    subject_guild_id: int | None = None
    scope_guild_id: int | None = None
    expires_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One recorded permission mutation."""

    id: int
    action: str
    at: Instant
    actor_account_id: int | None = None
    subject_kind: str | None = None
    subject_id: int | None = None
    subject_guild_id: int | None = None
    pattern: str | None = None
    role_id: int | None = None
    scope_guild_id: int | None = None
    effect: int | None = None
    reason: str | None = None


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
    ) -> None:
        """Create or overwrite the one grant for this pattern, subject and scope."""
        ...

    async def delete_grant(
        self,
        *,
        pattern: str,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        """Remove that grant, returning whether one existed; *audit* is appended only if so."""
        ...

    async def list_rules(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[RuleRow, ...]:
        """Direct grants matching every filter given, in insertion order.

        *guild_id* matches a grant held by that guild's roles or scoped to it.
        """
        ...

    async def list_assignments(
        self,
        *,
        subject_account_id: int | None = None,
        subject_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[AssignmentRow, ...]:
        """Role assignments matching every filter given, with each role's slug resolved."""
        ...

    async def list_roles(self, *, guild_id: int | None = None) -> tuple[RoleRecord, ...]:
        """Every global role, plus that guild's own roles when *guild_id* is given."""
        ...

    async def get_role(self, slug: str, *, guild_id: int | None = None) -> RoleRecord | None:
        """Resolve a slug, preferring the guild's own role over a global one of the same name."""
        ...

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
        """Insert a role and return it with its assigned id, which *audit* also records."""
        ...

    async def delete_role(self, role_id: int, *, audit: AuditEntry) -> bool:
        """Delete a role with its patterns, edges and assignments, returning whether it existed."""
        ...

    async def set_role_rank(self, role_id: int, rank: int, *, audit: AuditEntry) -> None:
        """Set the rank that decides who may edit this role."""
        ...

    async def set_role_pattern(
        self,
        role_id: int,
        pattern: str,
        mode: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        """Add *pattern* to the role as an include or an exclude, per *mode*.

        One mode per pattern per role, so re-adding with the other mode replaces rather than
        stacking and a role can never contradict itself.
        """
        ...

    async def remove_role_pattern(self, role_id: int, pattern: str, *, audit: AuditEntry) -> bool:
        """Drop *pattern* from the role in whichever mode it held, returning whether it was there."""
        ...

    async def add_role_include(
        self,
        role_id: int,
        included_role_id: int,
        *,
        added_by_account_id: int | None,
        audit: AuditEntry,
    ) -> None:
        """Make one role include another. Idempotent; cycles are tolerated by the resolver."""
        ...

    async def remove_role_include(self, role_id: int, included_role_id: int, *, audit: AuditEntry) -> bool:
        """Drop the composition edge, returning whether it existed."""
        ...

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
        """Assign the role to a subject, refreshing expiry and provenance if it already is."""
        ...

    async def unassign_role(
        self,
        role_id: int,
        *,
        subject_account_id: int | None,
        subject_role_id: int | None,
        scope_guild_id: int | None,
        audit: AuditEntry,
    ) -> bool:
        """Remove the assignment, returning whether one existed; *audit* is appended only if so."""
        ...

    async def list_audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[AuditRow, ...]:
        """The most recent *limit* audit rows, newest first, narrowed to one guild if given."""
        ...
