"""Application ports for permissions."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from whenever import Instant

from squid.permissions.domain import GlobalAdministrator


class GlobalAdministratorStore(Protocol):
    """Persistence operations required by :class:`AuthorizationService`.

    Superseded by :class:`PermissionStore`; retained until the legacy tiers go.
    """

    async def contains(self, account_id: int) -> bool: ...

    async def list(self) -> Sequence[GlobalAdministrator]: ...

    async def grant(self, account_id: int, granted_by_account_id: int) -> GlobalAdministrator: ...

    async def revoke(self, account_id: int) -> bool: ...


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
