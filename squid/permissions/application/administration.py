"""Create and change the permission grants and roles managed by `/perm` and `/role`.

A direct grant attaches an allow, deny, or forbid pattern to an account or Discord
role. A permission role bundles patterns and can be assigned to either kind of
subject. The actor is the person making the change, who may differ from its subject.

Two checks protect these changes. Authority limits actors to granting capabilities
they already hold. Management rank limits which permission roles they may edit:
equally privileged administrators cannot edit each other's roles just because
they hold the same capabilities. Protected built-in roles additionally reserve
structural changes for the bot owner.

Management rank does not affect permission resolution. Reordering roles therefore
cannot silently change which permission rule wins.
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.core.i18n import tr
from squid.permissions.application.ports import AuditEntry, PermissionAdminUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import CATALOGUE, BuiltinRoleKeys, Catalogue, Effect, Pattern
from squid.permissions.domain.catalogue import (
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid.permissions.models import PermissionAuditEntry, PermissionGrant, PermissionRole, PermissionRoleAssignment
from squid.unit_of_work import UnitOfWorkFactory

INCLUDE_MODE = 1
EXCLUDE_MODE = -1

MAX_ROLE_RANK = 999
"""Below `owner`, so no created role can ever be unmanageable by the owner."""


class PermissionAuthorityError(AuthorizationError):
    """The actor does not hold everything they are trying to give away."""

    default_title = tr(t"permissions-outside-authority = Outside your authority")


class PermissionRankError(AuthorizationError):
    """The target role is at or above the actor's own highest role."""

    default_title = tr(t"permissions-role-not-yours = Role is not yours to manage")


class ProtectedRoleError(AuthorizationError):
    """A built-in role cannot be restructured by anyone but the owner."""

    default_title = tr(t"permissions-built-role = Built-in role")


class PermissionAdministrationService:
    """Validate administrative changes and commit them with their audit entries.

    Keep delegation checks here so callers do not need to reproduce them in each
    interface that grants permissions or manages roles.
    """

    def __init__(
        self,
        permissions: PermissionService,
        *,
        uow_factory: UnitOfWorkFactory[PermissionAdminUnitOfWork],
        catalogue: Catalogue = CATALOGUE,
    ) -> None:
        self._uow_factory = uow_factory
        self._permissions = permissions
        self._catalogue = catalogue

    async def resolve_actor(self, subject: Actor) -> Actor:
        """Resolve the caller's capabilities and highest role rank in one transaction."""
        async with self._uow_factory() as uow:
            held = (
                frozenset()
                if subject.is_bot_owner
                else await self._permissions.capabilities_in(uow, subject, self._catalogue)
            )
            return replace(subject, capabilities=held, highest_permission_rank=await self._highest_rank(uow, subject))

    async def _highest_rank(self, uow: PermissionAdminUnitOfWork, subject: Actor) -> int:
        if subject.is_bot_owner:
            return MAX_ROLE_RANK + 1
        roles = {role.id: role for role in await uow.permission_admin.list_roles()}
        assignments = await uow.permission_admin.list_assignments(
            subject_account_id=subject.account_id,
            guild_id=subject.guild_id,
        )
        # The same two tests the resolver applies to a rule: an assignment that
        # confers nothing any more must not confer rank either. The store's
        # guild filter admits every account assignment whatever its scope, so
        # the scope test happens here rather than in SQL.
        now = Instant.now()
        held_ranks = [
            roles[assignment.role_id].rank
            for assignment in assignments
            if assignment.role_id in roles
            and _is_live(assignment.expires_at, now)
            and assignment.scope_guild_id in (None, subject.guild_id)
        ]
        # Discord's Manage Server implies the guild-admin built-in, so its rank
        # counts too; otherwise a server administrator could not manage any role
        # they had not also been explicitly assigned.
        if subject.discord_guild_admin:
            bridge = next((role for role in roles.values() if role.builtin_key == BuiltinRoleKeys.GUILD_ADMIN), None)
            if bridge is not None:
                held_ranks.append(bridge.rank)
        return max(held_ranks, default=0)

    # ---- Authorization checks ------------------------------------------

    def _require_authority(self, actor: Actor, patterns: Iterable[str]) -> None:
        """Reject patterns selecting any current node the actor does not hold."""
        if actor.is_bot_owner:
            return
        for pattern in patterns:
            reached = self._reached(pattern)
            if missing := sorted(reached - actor.capabilities):
                msg = f"{missing[0]} is outside your authority; you cannot grant what you do not hold."
                raise PermissionAuthorityError(msg, public_context={"node": missing[0], "pattern": pattern})

    def _require_rank(self, actor: Actor, role: PermissionRole) -> None:
        """Refuse managing a role at or above the actor's own highest rank."""
        if actor.is_bot_owner:
            return
        if role.rank >= actor.require_permission_rank():
            msg = (
                f"{role.slug} (rank {role.rank}) is at or above your highest role "
                f"(rank {actor.require_permission_rank()}); you cannot manage it."
            )
            raise PermissionRankError(msg, public_context={"role": role.slug, "rank": role.rank})

    def _require_unprotected(self, actor: Actor, role: PermissionRole) -> None:
        """Reserve structural edits to protected roles for the bot owner.

        Holding a role's capabilities and outranking it does not authorize changing
        that shared built-in definition.
        """
        if role.protected and not actor.is_bot_owner:
            msg = f"{role.slug} is a built-in role; only the bot owner can restructure it."
            raise ProtectedRoleError(msg, public_context={"role": role.slug})

    def _require_scope(self, actor: Actor, pattern: str, scope_guild_id: int | None) -> None:
        """Limit guild delegates to guild-scoped nodes in the current server.

        Global granters and the owner bypass this delegation restriction. The resolver
        also prevents guild-scoped grants from satisfying global nodes.
        """
        if actor.is_bot_owner or PERM_GRANT_GLOBAL.name in actor.capabilities:
            return
        if PERM_GRANT_GUILD.name not in actor.capabilities:
            msg = "You cannot grant permissions."
            raise PermissionAuthorityError(msg)
        if scope_guild_id is None or scope_guild_id != actor.guild_id:
            msg = "You may only grant permissions scoped to this server."
            raise PermissionAuthorityError(msg, public_context={"scope_guild_id": scope_guild_id})
        if not self._permissions.is_delegable_by_guild_admin(pattern):
            msg = f"{pattern} reaches permissions that are not this server's to grant."
            raise PermissionAuthorityError(msg, public_context={"pattern": pattern})

    def _require_assignment_scope(self, actor: Actor, leaves: Iterable[str], scope_guild_id: int | None) -> None:
        """Apply the delegation rule to a role's leaves as well as to raw patterns.

        An assignment carries its own scope into resolution and the role's own
        `guild_id` is not consulted there, so a guild delegate assigning a guild
        role at global scope would otherwise hold its nodes in every guild.
        """
        for pattern in leaves:
            self._require_scope(actor, pattern, scope_guild_id)

    def _require_role_management(self, actor: Actor, role: PermissionRole) -> None:
        node = ROLE_DEFINITION_MANAGE if role.guild_id is None else ROLE_DEFINITION_MANAGE_GUILD
        if not actor.is_bot_owner and node.name not in actor.capabilities:
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

    # ---- Direct grants -------------------------------------------------

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
        async with self._uow_factory() as uow:
            Pattern.parse(pattern)
            self._reached(pattern)
            if effect is Effect.FORBID:
                # Absolute and short-circuiting, so it is owner-only and must say why.
                if not actor.is_bot_owner:
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
            if discord_role_id is not None and guild_id is None:
                # Report the missing guild before the database constraint rejects it.
                msg = "Granting to a Discord role has to be done inside that role's server."
                raise ValidationError(msg, public_context={"discord_role_id": discord_role_id})

            await uow.permission_admin.upsert_grant(
                pattern=pattern,
                effect=int(effect),
                subject_account_id=account_id,
                subject_role_id=discord_role_id,
                subject_guild_id=guild_id if discord_role_id is not None else None,
                scope_guild_id=scope_guild_id,
                expires_at=expires_at,
                granted_by_account_id=actor.account_id,
                reason=reason,
                audit=AuditEntry(
                    action="grant",
                    actor_account_id=actor.account_id,
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
            await uow.commit()

    async def revoke(
        self,
        actor: Actor,
        *,
        pattern: str,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        scope_guild_id: int | None = None,
    ) -> bool:
        """Remove a stored rule, returning whether it existed.

        Remaining rules determine the result; the catalogue default applies only if
        none match.
        """
        async with self._uow_factory() as uow:
            self._require_scope(actor, pattern, scope_guild_id)
            self._require_authority(actor, (pattern,))
            if PERM_GRANT_GLOBAL.name in self._reached(pattern):
                await self._refuse_last_global_granter(uow, account_id, pattern)
            result = await uow.permission_admin.delete_grant(
                pattern=pattern,
                subject_account_id=account_id,
                subject_role_id=discord_role_id,
                scope_guild_id=scope_guild_id,
                audit=AuditEntry(
                    action="revoke",
                    actor_account_id=actor.account_id,
                    subject_kind="account" if account_id is not None else "discord_role",
                    subject_id=account_id if account_id is not None else discord_role_id,
                    pattern=pattern,
                    scope_guild_id=scope_guild_id,
                ),
            )
            await uow.commit()
            return result

    async def _refuse_last_global_granter(
        self, uow: PermissionAdminUnitOfWork, account_id: int | None, pattern: str
    ) -> None:
        """Reject revocation when at most one account has a live direct allow for global grants.

        This recovery safeguard counts matching direct grants, not resolved authority:
        role assignments, scope, and competing deny or forbid rules are not evaluated.
        """
        holders = await self._global_granter_count(uow)
        if holders <= 1:
            msg = "That would remove the last holder of perm.grant.global."
            raise ConflictError(msg, public_context={"pattern": pattern, "account_id": account_id})

    async def _global_granter_count(self, uow: PermissionAdminUnitOfWork) -> int:
        rules = await uow.permission_admin.list_rules()
        now = Instant.now()
        granting = {
            rule.subject_account_id
            for rule in rules
            if rule.effect == int(Effect.ALLOW)
            and rule.subject_account_id is not None
            and _is_live(rule.expires_at, now)
            and PERM_GRANT_GLOBAL.name in self._catalogue.expand(rule.pattern)
        }
        return len(granting)

    async def rules_for(
        self,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
    ) -> tuple[PermissionGrant, ...]:
        """List stored direct grants matching the supplied subject and guild filters."""
        async with self._uow_factory() as uow:
            return await uow.permission_admin.list_rules(
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
    ) -> tuple[PermissionRoleAssignment, ...]:
        """List permission-role assignments matching the supplied subject and guild filters."""
        async with self._uow_factory() as uow:
            return await uow.permission_admin.list_assignments(
                subject_account_id=account_id,
                subject_role_id=discord_role_id,
                guild_id=guild_id,
            )

    async def audit(self, *, guild_id: int | None = None, limit: int = 20) -> tuple[PermissionAuditEntry, ...]:
        """The most recent permission mutations."""
        async with self._uow_factory() as uow:
            return await uow.permission_admin.list_audit(guild_id=guild_id, limit=limit)

    # ---- Permission roles ----------------------------------------------

    async def roles(self, *, guild_id: int | None = None) -> tuple[PermissionRole, ...]:
        """Every role visible from one guild, highest rank first."""
        async with self._uow_factory() as uow:
            roles = await uow.permission_admin.list_roles(guild_id=guild_id)
            return tuple(sorted(roles, key=lambda role: (-role.rank, role.slug)))

    async def role(self, slug: str, *, guild_id: int | None = None) -> PermissionRole:
        async with self._uow_factory() as uow:
            found = await uow.permission_admin.get_role(slug, guild_id=guild_id)
            if found is None:
                msg = f"No permission role named {slug!r}."
                raise NotFoundError(msg, public_context={"role": slug})
            return found

    def resolved_leaves(self, role: PermissionRole, roles: Sequence[PermissionRole]) -> tuple[str, ...]:
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
    ) -> PermissionRole:
        """Create an empty role. Patterns are added separately, each gated."""
        async with self._uow_factory() as uow:
            if not 0 <= rank <= MAX_ROLE_RANK:
                msg = f"Role rank must be between 0 and {MAX_ROLE_RANK}."
                raise ValidationError(msg, public_context={"field": "rank"})
            node = ROLE_DEFINITION_MANAGE if guild_id is None else ROLE_DEFINITION_MANAGE_GUILD
            if not actor.is_bot_owner and node.name not in actor.capabilities:
                msg = f"You need {node.name} to create this role."
                raise PermissionAuthorityError(msg, public_context={"node": node.name})
            if not actor.is_bot_owner and rank >= actor.require_permission_rank():
                msg = f"You cannot create a role at rank {rank}; your own highest is {actor.require_permission_rank()}."
                raise PermissionRankError(msg, public_context={"rank": rank})
            if await uow.permission_admin.get_role(slug, guild_id=guild_id) is not None:
                msg = f"A role named {slug!r} already exists here."
                raise ConflictError(msg, public_context={"role": slug})
            result = await uow.permission_admin.create_role(
                slug=slug,
                name=name,
                description=description,
                guild_id=guild_id,
                rank=rank,
                created_by_account_id=actor.account_id,
                audit=AuditEntry(
                    action="role_create",
                    actor_account_id=actor.account_id,
                    subject_guild_id=guild_id,
                    reason=description,
                    details={"slug": slug, "rank": rank},
                ),
            )
            await uow.commit()
            return result

    async def delete_role(self, actor: Actor, role: PermissionRole) -> bool:
        async with self._uow_factory() as uow:
            self._require_role_management(actor, role)
            result = await uow.permission_admin.delete_role(
                role.id,
                audit=AuditEntry(
                    action="role_delete",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    subject_guild_id=role.guild_id,
                    details={"slug": role.slug},
                ),
            )
            await uow.commit()
            return result

    async def set_rank(self, actor: Actor, role: PermissionRole, rank: int) -> None:
        async with self._uow_factory() as uow:
            if not 0 <= rank <= MAX_ROLE_RANK:
                msg = f"Role rank must be between 0 and {MAX_ROLE_RANK}."
                raise ValidationError(msg, public_context={"field": "rank"})
            self._require_role_management(actor, role)
            if not actor.is_bot_owner and rank >= actor.require_permission_rank():
                msg = f"You cannot set a rank at or above your own highest ({actor.require_permission_rank()})."
                raise PermissionRankError(msg, public_context={"rank": rank})
            await uow.permission_admin.set_role_rank(
                role.id,
                rank,
                audit=AuditEntry(
                    action="role_rank",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    details={"slug": role.slug, "rank": rank},
                ),
            )
            await uow.commit()

    async def add_pattern(self, actor: Actor, role: PermissionRole, pattern: str, *, mode: int = INCLUDE_MODE) -> None:
        """Include or exclude a pattern from a role.

        Includes require the actor to hold every selected node. Exclusions only narrow
        the role, so they require management authority but not the excluded permissions.
        """
        async with self._uow_factory() as uow:
            Pattern.parse(pattern)
            self._reached(pattern)
            self._require_role_management(actor, role)
            if mode == INCLUDE_MODE:
                self._require_authority(actor, (pattern,))
                if role.guild_id is not None and not self._permissions.is_delegable_by_guild_admin(pattern):
                    msg = f"{pattern} reaches permissions outside this server's scope."
                    raise PermissionAuthorityError(msg, public_context={"pattern": pattern})
            await uow.permission_admin.set_role_pattern(
                role.id,
                pattern,
                mode,
                added_by_account_id=actor.account_id,
                audit=AuditEntry(
                    action="role_include" if mode == INCLUDE_MODE else "role_exclude",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    pattern=pattern,
                    details={"slug": role.slug},
                ),
            )
            await uow.commit()

    async def remove_pattern(self, actor: Actor, role: PermissionRole, pattern: str) -> bool:
        async with self._uow_factory() as uow:
            self._require_role_management(actor, role)
            result = await uow.permission_admin.remove_role_pattern(
                role.id,
                pattern,
                audit=AuditEntry(
                    action="role_pattern_remove",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    pattern=pattern,
                    details={"slug": role.slug},
                ),
            )
            await uow.commit()
            return result

    async def add_include(self, actor: Actor, role: PermissionRole, included: PermissionRole) -> None:
        """Compose one role into another.

        Refuse cycles, and refuse a guild role unless `role` belongs to the same guild.
        """
        async with self._uow_factory() as uow:
            self._require_role_management(actor, role)
            if role.id == included.id:
                msg = "A role cannot include itself."
                raise ValidationError(msg)
            # A guild role inside a global role would let one guild's admins change what the
            # global role grants in every guild.
            if included.guild_id is not None and included.guild_id != role.guild_id:
                msg = f"{role.slug} can only include global roles or roles from its own guild."
                raise ValidationError(msg, public_context={"role": role.slug, "included": included.slug})
            roles = await uow.permission_admin.list_roles()
            if self._would_cycle(role.id, included.id, roles):
                msg = f"Including {included.slug} in {role.slug} would create a cycle."
                raise ConflictError(msg, public_context={"role": role.slug, "included": included.slug})
            # Check inherited capabilities just as we check direct include patterns.
            self._require_authority(actor, self.resolved_leaves(included, roles))
            await uow.permission_admin.add_role_include(
                role.id,
                included.id,
                added_by_account_id=actor.account_id,
                audit=AuditEntry(
                    action="role_compose",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    details={"slug": role.slug, "included": included.slug},
                ),
            )
            await uow.commit()

    @staticmethod
    def _would_cycle(role_id: int, included_id: int, roles: Sequence[PermissionRole]) -> bool:
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

    async def remove_include(self, actor: Actor, role: PermissionRole, included: PermissionRole) -> bool:
        async with self._uow_factory() as uow:
            self._require_role_management(actor, role)
            result = await uow.permission_admin.remove_role_include(
                role.id,
                included.id,
                audit=AuditEntry(
                    action="role_decompose",
                    actor_account_id=actor.account_id,
                    role_id=role.id,
                    details={"slug": role.slug, "included": included.slug},
                ),
            )
            await uow.commit()
            return result

    async def assign(
        self,
        actor: Actor,
        role: PermissionRole,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        guild_id: int | None = None,
        scope_guild_id: int | None = None,
        expires_at: Instant | None = None,
        reason: str | None = None,
    ) -> None:
        """Give a role to an account or to everyone holding a Discord role."""
        async with self._uow_factory() as uow:
            if (account_id is None) == (discord_role_id is None):
                msg = "A role is assigned to exactly one subject: an account or a Discord role."
                raise ValidationError(msg)
            if discord_role_id is not None and guild_id is None:
                msg = "Assigning to a Discord role has to be done inside that role's server."
                raise ValidationError(msg, public_context={"discord_role_id": discord_role_id})
            self._require_rank(actor, role)
            roles = await uow.permission_admin.list_roles()
            leaves = self.resolved_leaves(role, roles)
            self._require_assignment_scope(actor, leaves, scope_guild_id)
            self._require_authority(actor, leaves)
            await uow.permission_admin.assign_role(
                role.id,
                subject_account_id=account_id,
                subject_role_id=discord_role_id,
                subject_guild_id=guild_id if discord_role_id is not None else None,
                scope_guild_id=scope_guild_id,
                expires_at=expires_at,
                granted_by_account_id=actor.account_id,
                reason=reason,
                audit=AuditEntry(
                    action="role_assign",
                    actor_account_id=actor.account_id,
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
            await uow.commit()

    async def unassign(
        self,
        actor: Actor,
        role: PermissionRole,
        *,
        account_id: int | None = None,
        discord_role_id: int | None = None,
        scope_guild_id: int | None = None,
    ) -> bool:
        async with self._uow_factory() as uow:
            self._require_rank(actor, role)
            if role.builtin_key == BuiltinRoleKeys.OWNER:
                msg = "The owner role cannot be unassigned."
                raise ConflictError(msg, public_context={"role": role.slug})
            roles = await uow.permission_admin.list_roles()
            self._require_assignment_scope(actor, self.resolved_leaves(role, roles), scope_guild_id)
            result = await uow.permission_admin.unassign_role(
                role.id,
                subject_account_id=account_id,
                subject_role_id=discord_role_id,
                scope_guild_id=scope_guild_id,
                audit=AuditEntry(
                    action="role_unassign",
                    actor_account_id=actor.account_id,
                    subject_kind="account" if account_id is not None else "discord_role",
                    subject_id=account_id if account_id is not None else discord_role_id,
                    role_id=role.id,
                    scope_guild_id=scope_guild_id,
                    details={"slug": role.slug},
                ),
            )
            await uow.commit()
            return result

    def unmanageable_patterns(
        self, actor: Actor, role: PermissionRole, roles: Sequence[PermissionRole]
    ) -> tuple[str, ...]:
        """Return role capabilities the actor cannot grant, for administrative displays."""
        if actor.is_bot_owner:
            return ()
        return tuple(sorted(frozenset(self.resolved_leaves(role, roles)) - actor.capabilities))


def _is_live(expires_at: Instant | None, now: Instant) -> bool:
    """Whether a stored row still confers anything, on the resolver's terms."""
    return expires_at is None or expires_at > now


def scope_label(scope_guild_id: int | None) -> str:
    """How a rule's scope reads in `/perm list` and `/perm can`."""
    return "global" if scope_guild_id is None else f"guild {scope_guild_id}"


def effect_label(effect: int) -> str:
    """Return the command display label for an effect, or `?` for an unknown value."""
    return {int(Effect.ALLOW): "allow", int(Effect.DENY): "deny", int(Effect.FORBID): "forbid"}.get(effect, "?")
