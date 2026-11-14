"""Load a subject's permission sources and resolve the capabilities they hold.

Direct grants attach patterns to an account or Discord role. Permission-role
assignments attach named bundles of patterns instead; these are Squid roles,
separate from Discord roles. The service expands both sources into rules for the
domain resolver. Discord's Manage Server permission also supplies the built-in
guild-admin role, through what we call the guild-admin bridge.

Assembled rules can be cached across checks. Resolution still evaluates expiry,
scope, and precedence for each requested capability.
"""

from contextlib import nullcontext

from whenever import Instant

from squid.core.actors import Actor
from squid.permissions.application.cache import SubjectRuleCache, cache_key
from squid.permissions.application.ports import PermissionUnitOfWork, SubjectRecords
from squid.permissions.application.roles import role_specs
from squid.permissions.domain import (
    CATALOGUE,
    BuiltinRoleKeys,
    Catalogue,
    CheckMode,
    Effect,
    Origin,
    Pattern,
    PermissionCheck,
    PermissionNode,
    RoleSpec,
    Rule,
    SubjectKind,
    expand_role,
    resolve,
    rules_from_role,
)
from squid.permissions.errors import require_check
from squid.permissions.models import PermissionGrant
from squid.unit_of_work import UnitOfWorkFactory


def _grant_rule(record: PermissionGrant) -> Rule:
    by_account = record.subject_account_id is not None
    return Rule(
        pattern=Pattern.parse(record.pattern),
        effect=Effect(record.effect),
        subject_kind=SubjectKind.ACCOUNT if by_account else SubjectKind.DISCORD_ROLE,
        origin=Origin.ACCOUNT_GRANT if by_account else Origin.DISCORD_ROLE_GRANT,
        scope_guild_id=record.scope_guild_id,
        expires_at=record.expires_at,
        source=f"account:{record.subject_account_id}" if by_account else f"discord-role:{record.subject_role_id}",
    )


class PermissionService:
    """Answer permission questions for one subject at a time."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[PermissionUnitOfWork],
        catalogue: Catalogue = CATALOGUE,
        cache: SubjectRuleCache | None = None,
    ):
        self._uow_factory = uow_factory
        self._catalogue = catalogue
        self._cache = cache

    @property
    def catalogue(self) -> Catalogue:
        """The node catalogue this service resolves against."""
        return self._catalogue

    async def check(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        """Evaluate requested nodes once, retaining both grants and denial reasons.

        Supplying `uow` reads its current rules without consulting or populating
        the cache, and leaves its transaction lifecycle to the caller. Otherwise
        the service uses its normal cached lookup.
        """
        rules = await self._rules_for(subject, uow=uow)
        now = Instant.now()
        return PermissionCheck(
            tuple(resolve(node, subject, rules, catalogue=self._catalogue, now=now) for node in nodes), mode=mode
        )

    async def require(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> None:
        """Enforce the same check and raise the shared explanatory ACL error on denial."""
        require_check(await self.check(subject, *nodes, uow=uow, mode=mode))

    async def _rules_for(self, subject: Actor, *, uow: PermissionUnitOfWork | None) -> tuple[Rule, ...]:
        if subject.is_bot_owner:
            return ()
        cache = self._cache if uow is None else None
        key = cache_key(subject)
        if cache is not None and (cached := cache.get(key)) is not None:
            return cached
        async with self._uow_factory() if uow is None else nullcontext(uow) as scope:
            records = await scope.permissions.load_for_subject(
                account_id=subject.account_id,
                discord_role_ids=subject.discord_role_ids,
                guild_id=subject.guild_id,
            )
        rules = self._assemble(records, subject)
        if cache is not None:
            cache.put(key, rules, epoch=records.epoch)
        return rules

    def _assemble(self, records: SubjectRecords, subject: Actor) -> tuple[Rule, ...]:
        """Convert stored grants and role assignments into rules, including the guild-admin bridge."""
        specs = role_specs(records.roles)
        rules = [_grant_rule(grant) for grant in records.grants]
        rules.extend(self._assignment_rules(records, specs))

        bridge = self._bridge_rules(records, specs, subject)
        rules.extend(bridge)
        return tuple(rules)

    def _assignment_rules(self, records: SubjectRecords, specs: dict[str, RoleSpec]) -> list[Rule]:
        slugs = {str(role.id): role.slug for role in records.roles}
        rules: list[Rule] = []
        for assignment in records.assignments:
            key = str(assignment.role_id)
            if key not in specs:
                continue
            by_account = assignment.subject_account_id is not None
            rules.extend(
                rules_from_role(
                    expand_role(key, specs, catalogue=self._catalogue),
                    subject_kind=SubjectKind.ACCOUNT if by_account else SubjectKind.DISCORD_ROLE,
                    origin=Origin.ROLE,
                    scope_guild_id=assignment.scope_guild_id,
                    expires_at=assignment.expires_at,
                    source=f"role:{slugs.get(key, key)}",
                    via=None if by_account else f"discord-role:{assignment.subject_role_id}",
                )
            )
        return rules

    def _bridge_rules(
        self,
        records: SubjectRecords,
        specs: dict[str, RoleSpec],
        subject: Actor,
    ) -> list[Rule]:
        """Add guild-admin rules for Discord members with Manage Server.

        The bridge has the lowest origin rank. Specificity, scope, and subject rank
        still take precedence, so an explicit rule does not always outrank it.
        """
        if not subject.discord_guild_admin or subject.guild_id is None:
            return []
        key = next((str(role.id) for role in records.roles if role.builtin_key == BuiltinRoleKeys.GUILD_ADMIN), None)
        if key is None:
            return []
        return list(
            rules_from_role(
                expand_role(key, specs, catalogue=self._catalogue),
                subject_kind=SubjectKind.DISCORD_ROLE,
                origin=Origin.GUILD_ADMIN_BRIDGE,
                scope_guild_id=subject.guild_id,
                source="discord:manage-server",
            )
        )
