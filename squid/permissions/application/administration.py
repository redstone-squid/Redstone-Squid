"""Writing permissions: `/perm` and `/role`, and the guards behind them.

Two independent gates protect every role edit and every grant, and they block
different attacks:

* the **authority boundary** stops escalation — you cannot grant, or edit a role
  into a state carrying, a node you do not hold yourself;
* **rank** stops lateral sabotage — two equally privileged administrators editing
  or deleting each other's roles, which the boundary alone permits because
  neither gains anything by it.

They are separate because they fail for different reasons, and a single
"insufficient permissions" for both is what makes a permission system
infuriating. Every refusal below says which gate refused and why.

`rank` never enters resolution (property P10). That is what makes having both
gates safe: reordering roles for management reasons cannot silently change an
authorization outcome.
"""

from collections.abc import Iterable, Sequence

from whenever import Instant

from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.core.i18n import _
from squid.permissions.application.ports import (
    AssignmentRow,
    AuditEntry,
    AuditRow,
    PermissionAdminStore,
    RoleRecord,
    RuleRow,
)
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import (
    CATALOGUE,
    BuiltinRoleKeys,
    Catalogue,
    Effect,
    NodeScope,
    Pattern,
    Subject,
)
from squid.permissions.domain.catalogue import (
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)

INCLUDE_MODE = 1
EXCLUDE_MODE = -1

MAX_ROLE_RANK = 999
"""Below `owner`, so no created role can ever be unmanageable by the owner."""


class PermissionAuthorityError(AuthorizationError):
    """The actor does not hold everything they are trying to give away."""

    default_title = _("Outside your authority")


class PermissionRankError(AuthorizationError):
    """The target role is at or above the actor's own highest role."""

    default_title = _("Role is not yours to manage")


class ProtectedRoleError(AuthorizationError):
    """A built-in role cannot be restructured by anyone but the owner."""

    default_title = _("Built-in role")


class Actor:
    """Who is performing an administrative action, and what they may do.

    Built once per command so the gates below share one permission load.
    """

    def __init__(self, subject: Subject, *, held: frozenset[str], highest_rank: int) -> None:
        self.subject = subject
        self.held = held
        self.highest_rank = highest_rank

    @property
    def is_owner(self) -> bool:
        return self.subject.is_bot_owner


class PermissionAdministrationService:
    """Grant, revoke, and shape roles, with the delegation rules enforced here.

    Deliberately not in the cog: the API and the recovery CLI reach the same
    rules through this class, and a guard that lives in one transport is a guard
    the other transports do not have.
    """

    def __init__(
        self,
        store: PermissionAdminStore,
        permissions: PermissionService,
        *,
        catalogue: Catalogue = CATALOGUE,
    ) -> None:
        self._store = store
        self._permissions = permissions
        self._catalogue = catalogue

    async def actor(self, subject: Subject) -> Actor:
        """Resolve everything the gates need about the caller, in one load."""
        held = frozenset() if subject.is_bot_owner else await self._permissions.capabilities(subject, self._catalogue)
        return Actor(subject, held=held, highest_rank=await self._highest_rank(subject))

    async def _highest_rank(self, subject: Subject) -> int:
        if subject.is_bot_owner:
            return MAX_ROLE_RANK + 1
        roles = {role.id: role for role in await self._store.list_roles()}
        assignments = await self._store.list_assignments(
            subject_account_id=subject.account_id,
            guild_id=subject.guild_id,
        )
        held_ranks = [roles[assignment.role_id].rank for assignment in assignments if assignment.role_id in roles]
        # Discord's Manage Server implies the guild-admin built-in, so its rank
        # counts too; otherwise a server administrator could not manage any role
        # they had not also been explicitly assigned.
        if subject.discord_guild_admin:
            bridge = next((role for role in roles.values() if role.builtin_key == BuiltinRoleKeys.GUILD_ADMIN), None)
            if bridge is not None:
                held_ranks.append(bridge.rank)
        return max(held_ranks, default=0)

    # ---- gates ---------------------------------------------------------

    def _require_authority(self, actor: Actor, patterns: Iterable[str]) -> None:
        """Refuse patterns reaching any node the actor does not hold.

        Self-maintaining: it tracks real authority rather than a hand-kept
        integer that drifts as the catalogue grows.
        """
        if actor.is_owner:
            return
        for pattern in patterns:
            reached = self._reached(pattern)
            if missing := sorted(reached - actor.held):
                msg = f"{missing[0]} is outside your authority; you cannot grant what you do not hold."
                raise PermissionAuthorityError(msg, public_context={"node": missing[0], "pattern": pattern})

    def _require_rank(self, actor: Actor, role: RoleRecord) -> None:
        """Refuse managing a role at or above the actor's own highest rank."""
        if actor.is_owner:
            return
        if role.rank >= actor.highest_rank:
            msg = (
                f"{role.slug} (rank {role.rank}) is at or above your highest role "
                f"(rank {actor.highest_rank}); you cannot manage it."
            )
            raise PermissionRankError(msg, public_context={"role": role.slug, "rank": role.rank})

    def _require_unprotected(self, actor: Actor, role: RoleRecord) -> None:
        """Keep the built-ins whole against anyone but the owner.

        Without this, someone who happens to hold everything in `global-admin`
        could hollow it out for everybody else, and both other gates would let
        them: the boundary is satisfied, and the rank check passes for anyone
        ranked above it.
        """
        if role.protected and not actor.is_owner:
            msg = f"{role.slug} is a built-in role; only the bot owner can restructure it."
            raise ProtectedRoleError(msg, public_context={"role": role.slug})

    def _require_scope(self, actor: Actor, pattern: str, scope_guild_id: int | None) -> None:
        """Enforce the guild-delegation rule: guild-scoped rules, guild-only nodes.

        Checked here as well as by the resolver and a database CHECK. Three
        layers because this is the escalation that matters: a guild
        administrator must never be able to reach global state.
        """
        if actor.is_owner or PERM_GRANT_GLOBAL.name in actor.held:
            return
        if PERM_GRANT_GUILD.name not in actor.held:
            msg = "You cannot grant permissions."
            raise PermissionAuthorityError(msg)
        if scope_guild_id is None or scope_guild_id != actor.subject.guild_id:
            msg = "You may only grant permissions scoped to this server."
            raise PermissionAuthorityError(msg, public_context={"scope_guild_id": scope_guild_id})
        if not self._permissions.is_delegable_by_guild_admin(pattern):
            msg = f"{pattern} reaches permissions that are not this server's to grant."
            raise PermissionAuthorityError(msg, public_context={"pattern": pattern})

    def _require_role_management(self, actor: Actor, role: RoleRecord) -> None:
        node = ROLE_DEFINITION_MANAGE if role.guild_id is None else ROLE_DEFINITION_MANAGE_GUILD
        if not actor.is_owner and node.name not in actor.held:
            msg = f"You need {node.name} to manage this role."
            raise PermissionAuthorityError(msg, public_context={"node": node.name})
        self._require_unprotected(actor, role)
        self._require_rank(actor, role)

    def _reached(self, pattern: str) -> frozenset[str]:
        # A malformed pattern raises InvalidPatternError from the catalogue; a
        # well-formed one matching nothing is refused here, because "every leaf
        # it reaches is allowed" is vacuously true of a typo.
        reached = self._catalogue.expand(pattern)
        if not reached:
            msg = f"{pattern} matches no permission node."
            raise ValidationError(msg, public_context={"pattern": pattern})
        return reached

    # ---- rules ---------------------------------------------------------

    async def grant(
        self,
        actor: Actor,
        *,
        pattern: str,
        effect: Effect = Effect.ALLOW,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
        scope_guild_id: int | None = None,
        expires_at: Instant | None = None,
        reason: str | None = None,
    ) -> None:
        """Write one allow, deny or forbid rule."""
        Pattern.parse(pattern)
        self._reached(pattern)
        if effect is Effect.FORBID:
            # Absolute and short-circuiting, so it is owner-only and must say why.
            if not actor.is_owner:
                msg = "Only the bot owner can forbid a permission."
                raise PermissionAuthorityError(msg)
            if not reason:
                msg = "A forbid rule requires a reason."
                raise ValidationError(msg, public_context={"field": "reason"})
        self._require_scope(actor, pattern, scope_guild_id)
        self._require_authority(actor, (pattern,))
        if (account_id is None) == (discord_role_id is None):
            msg = "A rule applies to exactly one subject: an account or a Discord role."
            raise ValidationError(msg)

        await self._store.upsert_grant(
            pattern=pattern,
            effect=int(effect),
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            subject_guild_id=guild_id if discord_role_id is not None else None,
            scope_guild_id=scope_guild_id,
            expires_at=expires_at,
            granted_by_account_id=actor.subject.account_id,
            reason=reason,
            audit=AuditEntry(
                action="grant",
                actor_account_id=actor.subject.account_id,
                subject_kind="account" if account_id is not None else "discord_role",
                subject_id=account_id if account_id is not None else discord_role_id,
                subject_guild_id=guild_id,
                pattern=pattern,
                scope_guild_id=scope_guild_id,
                effect=int(effect),
                expires_at=expires_at,
                reason=reason,
            ),
        )

    async def revoke(
        self,
        actor: Actor,
        *,
        pattern: str,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        scope_guild_id: int | None = None,
    ) -> bool:
        """Remove a rule. Distinct from denying it: absence falls through to the default."""
        self._require_scope(actor, pattern, scope_guild_id)
        self._require_authority(actor, (pattern,))
        if PERM_GRANT_GLOBAL.name in self._reached(pattern):
            await self._refuse_last_global_granter(account_id, pattern)
        return await self._store.delete_grant(
            pattern=pattern,
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            scope_guild_id=scope_guild_id,
            audit=AuditEntry(
                action="revoke",
                actor_account_id=actor.subject.account_id,
                subject_kind="account" if account_id is not None else "discord_role",
                subject_id=account_id if account_id is not None else discord_role_id,
                pattern=pattern,
                scope_guild_id=scope_guild_id,
            ),
        )

    async def _refuse_last_global_granter(self, account_id: int | None, pattern: str) -> None:
        """Never leave the deployment with nobody who can grant anything.

        The owner escape hatch exists, but a deployment whose `owner_id` is
        misconfigured would otherwise be locked out entirely, and discovering
        that costs a database session.
        """
        holders = await self._global_granter_count()
        if holders <= 1:
            msg = "That would remove the last holder of perm.grant.global."
            raise ConflictError(msg, public_context={"pattern": pattern, "account_id": account_id})

    async def _global_granter_count(self) -> int:
        rules = await self._store.list_rules()
        granting = {
            rule.subject_account_id
            for rule in rules
            if rule.effect == int(Effect.ALLOW)
            and rule.subject_account_id is not None
            and PERM_GRANT_GLOBAL.name in self._catalogue.expand(rule.pattern)
        }
        return len(granting)

    async def rules_for(
        self,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[RuleRow, ...]:
        """Every stored rule attached to one subject."""
        return await self._store.list_rules(
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            guild_id=guild_id,
        )

    async def assignments_for(
        self,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[AssignmentRow, ...]:
        """Every role assignment attached to one subject."""
        return await self._store.list_assignments(
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            guild_id=guild_id,
        )

    async def audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[AuditRow, ...]:
        """The most recent permission mutations."""
        return await self._store.list_audit(guild_id=guild_id, limit=limit)

    # ---- roles ---------------------------------------------------------

    async def roles(self, *, guild_id: int | None = None) -> tuple[RoleRecord, ...]:
        """Every role visible from one guild, highest rank first."""
        roles = await self._store.list_roles(guild_id=guild_id)
        return tuple(sorted(roles, key=lambda role: (-role.rank, role.slug)))

    async def role(self, slug: str, *, guild_id: int | None = None) -> RoleRecord:
        found = await self._store.get_role(slug, guild_id=guild_id)
        if found is None:
            msg = f"No permission role named {slug!r}."
            raise NotFoundError(msg, public_context={"role": slug})
        return found

    def resolved_leaves(self, role: RoleRecord, roles: Sequence[RoleRecord]) -> tuple[str, ...]:
        """The node names a role actually confers, composition and subtraction applied."""
        return self._permissions.role_leaves(role, roles)

    async def create_role(
        self,
        actor: Actor,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        guild_id: int | None = None,
        rank: int = 0,
    ) -> RoleRecord:
        """Create an empty role. Patterns are added separately, each gated."""
        if not 0 <= rank <= MAX_ROLE_RANK:
            msg = f"Role rank must be between 0 and {MAX_ROLE_RANK}."
            raise ValidationError(msg, public_context={"field": "rank"})
        node = ROLE_DEFINITION_MANAGE if guild_id is None else ROLE_DEFINITION_MANAGE_GUILD
        if not actor.is_owner and node.name not in actor.held:
            msg = f"You need {node.name} to create this role."
            raise PermissionAuthorityError(msg, public_context={"node": node.name})
        if not actor.is_owner and rank >= actor.highest_rank:
            msg = f"You cannot create a role at rank {rank}; your own highest is {actor.highest_rank}."
            raise PermissionRankError(msg, public_context={"rank": rank})
        if await self._store.get_role(slug, guild_id=guild_id) is not None:
            msg = f"A role named {slug!r} already exists here."
            raise ConflictError(msg, public_context={"role": slug})
        return await self._store.create_role(
            slug=slug,
            name=name,
            description=description,
            guild_id=guild_id,
            rank=rank,
            created_by_account_id=actor.subject.account_id,
            audit=AuditEntry(
                action="role_create",
                actor_account_id=actor.subject.account_id,
                subject_guild_id=guild_id,
                reason=description,
                details={"slug": slug, "rank": rank},
            ),
        )

    async def delete_role(self, actor: Actor, role: RoleRecord) -> bool:
        self._require_role_management(actor, role)
        return await self._store.delete_role(
            role.id,
            audit=AuditEntry(
                action="role_delete",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                subject_guild_id=role.guild_id,
                details={"slug": role.slug},
            ),
        )

    async def set_rank(self, actor: Actor, role: RoleRecord, rank: int) -> None:
        if not 0 <= rank <= MAX_ROLE_RANK:
            msg = f"Role rank must be between 0 and {MAX_ROLE_RANK}."
            raise ValidationError(msg, public_context={"field": "rank"})
        self._require_role_management(actor, role)
        if not actor.is_owner and rank >= actor.highest_rank:
            msg = f"You cannot set a rank at or above your own highest ({actor.highest_rank})."
            raise PermissionRankError(msg, public_context={"rank": rank})
        await self._store.set_role_rank(
            role.id,
            rank,
            audit=AuditEntry(
                action="role_rank",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                details={"slug": role.slug, "rank": rank},
            ),
        )

    async def add_pattern(self, actor: Actor, role: RoleRecord, pattern: str, *, mode: int = INCLUDE_MODE) -> None:
        """Include or subtract one pattern in a role.

        An *exclusion* is not gated by the authority boundary: subtracting from a
        role can only ever narrow what it confers, so requiring the node would
        stop an administrator from removing a capability they cannot themselves
        hold — which is backwards.
        """
        Pattern.parse(pattern)
        self._reached(pattern)
        self._require_role_management(actor, role)
        if mode == INCLUDE_MODE:
            self._require_authority(actor, (pattern,))
            if role.guild_id is not None and not self._permissions.is_delegable_by_guild_admin(pattern):
                msg = f"{pattern} reaches permissions outside this server's scope."
                raise PermissionAuthorityError(msg, public_context={"pattern": pattern})
        await self._store.set_role_pattern(
            role.id,
            pattern,
            mode,
            added_by_account_id=actor.subject.account_id,
            audit=AuditEntry(
                action="role_include" if mode == INCLUDE_MODE else "role_exclude",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                pattern=pattern,
                details={"slug": role.slug},
            ),
        )

    async def remove_pattern(self, actor: Actor, role: RoleRecord, pattern: str) -> bool:
        self._require_role_management(actor, role)
        return await self._store.remove_role_pattern(
            role.id,
            pattern,
            audit=AuditEntry(
                action="role_pattern_remove",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                pattern=pattern,
                details={"slug": role.slug},
            ),
        )

    async def add_include(self, actor: Actor, role: RoleRecord, included: RoleRecord) -> None:
        """Compose one role into another, refusing cycles at write time."""
        self._require_role_management(actor, role)
        if role.id == included.id:
            msg = "A role cannot include itself."
            raise ValidationError(msg)
        roles = await self._store.list_roles()
        if self._would_cycle(role.id, included.id, roles):
            msg = f"Including {included.slug} in {role.slug} would create a cycle."
            raise ConflictError(msg, public_context={"role": role.slug, "included": included.slug})
        # Composing a role in confers everything it confers, so the boundary
        # applies to the composed leaves exactly as it does to a raw pattern.
        self._require_authority(actor, self.resolved_leaves(included, roles))
        await self._store.add_role_include(
            role.id,
            included.id,
            added_by_account_id=actor.subject.account_id,
            audit=AuditEntry(
                action="role_compose",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                details={"slug": role.slug, "included": included.slug},
            ),
        )

    @staticmethod
    def _would_cycle(role_id: int, included_id: int, roles: Sequence[RoleRecord]) -> bool:
        edges = {role.id: role.includes_roles for role in roles}
        seen: set[int] = set()
        frontier = [included_id]
        while frontier:
            current = frontier.pop()
            if current == role_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(edges.get(current, ()))
        return False

    async def remove_include(self, actor: Actor, role: RoleRecord, included: RoleRecord) -> bool:
        self._require_role_management(actor, role)
        return await self._store.remove_role_include(
            role.id,
            included.id,
            audit=AuditEntry(
                action="role_decompose",
                actor_account_id=actor.subject.account_id,
                role_id=role.id,
                details={"slug": role.slug, "included": included.slug},
            ),
        )

    async def assign(
        self,
        actor: Actor,
        role: RoleRecord,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
        scope_guild_id: int | None = None,
        expires_at: Instant | None = None,
        reason: str | None = None,
    ) -> None:
        """Give a role to an account or to everyone holding a Discord role."""
        if (account_id is None) == (discord_role_id is None):
            msg = "A role is assigned to exactly one subject: an account or a Discord role."
            raise ValidationError(msg)
        self._require_rank(actor, role)
        roles = await self._store.list_roles()
        self._require_authority(actor, self.resolved_leaves(role, roles))
        await self._store.assign_role(
            role.id,
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            subject_guild_id=guild_id if discord_role_id is not None else None,
            scope_guild_id=scope_guild_id,
            expires_at=expires_at,
            granted_by_account_id=actor.subject.account_id,
            reason=reason,
            audit=AuditEntry(
                action="role_assign",
                actor_account_id=actor.subject.account_id,
                subject_kind="account" if account_id is not None else "discord_role",
                subject_id=account_id if account_id is not None else discord_role_id,
                subject_guild_id=guild_id,
                role_id=role.id,
                scope_guild_id=scope_guild_id,
                expires_at=expires_at,
                reason=reason,
                details={"slug": role.slug},
            ),
        )

    async def unassign(
        self,
        actor: Actor,
        role: RoleRecord,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        scope_guild_id: int | None = None,
    ) -> bool:
        self._require_rank(actor, role)
        if role.builtin_key == BuiltinRoleKeys.OWNER:
            msg = "The owner role cannot be unassigned."
            raise ConflictError(msg, public_context={"role": role.slug})
        return await self._store.unassign_role(
            role.id,
            subject_account_id=account_id,
            subject_role_id=discord_role_id,
            scope_guild_id=scope_guild_id,
            audit=AuditEntry(
                action="role_unassign",
                actor_account_id=actor.subject.account_id,
                subject_kind="account" if account_id is not None else "discord_role",
                subject_id=account_id if account_id is not None else discord_role_id,
                role_id=role.id,
                scope_guild_id=scope_guild_id,
                details={"slug": role.slug},
            ),
        )

    def unmanageable_patterns(self, actor: Actor, role: RoleRecord, roles: Sequence[RoleRecord]) -> tuple[str, ...]:
        """Which of a role's leaves the actor could not grant themselves.

        `/role show` surfaces this so the two gates disagreeing is visible up
        front, rather than discovered one rejected edit at a time.
        """
        if actor.is_owner:
            return ()
        return tuple(sorted(frozenset(self.resolved_leaves(role, roles)) - actor.held))


def scope_label(scope_guild_id: int | None) -> str:
    """How a rule's scope reads in `/perm list` and `/perm explain`."""
    return "global" if scope_guild_id is None else f"guild {scope_guild_id}"


def effect_label(effect: int) -> str:
    """Discord's own vocabulary: allow / deny, plus the loud third one."""
    return {int(Effect.ALLOW): "allow", int(Effect.DENY): "deny", int(Effect.FORBID): "forbid"}.get(effect, "?")


def node_scope_label(scope: NodeScope) -> str:
    return scope.value
