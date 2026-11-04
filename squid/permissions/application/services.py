"""Load a subject's permission sources and resolve the capabilities they hold.

Direct grants attach patterns to an account or Discord role. Permission-role
assignments attach named bundles of patterns instead; these are Squid roles,
separate from Discord roles. The service expands both sources into rules for the
domain resolver. Discord's Manage Server permission also supplies the built-in
guild-admin role, through what we call the guild-admin bridge.

Assembled rules can be cached across checks. Resolution still evaluates expiry,
scope, and precedence for each requested capability.
"""

from collections.abc import Iterable

from squid.permissions.application.cache import SubjectRuleCache, cache_key
from squid.permissions.application.ports import PermissionUnitOfWork, SubjectRecords
from squid.permissions.domain import (
    BUILTIN_ROLES_BY_KEY,
    CATALOGUE,
    BuiltinRoleKeys,
    Catalogue,
    Decision,
    Effect,
    InvalidPatternError,
    NodeScope,
    Origin,
    Pattern,
    PermissionNode,
    RoleSpec,
    Rule,
    Subject,
    SubjectKind,
    expand_role,
    resolve,
    resolve_many,
    rules_from_role,
)
from squid.permissions.models import PermissionGrant, PermissionRole
from squid.unit_of_work import UnitOfWorkFactory


def _role_specs(roles: Iterable[PermissionRole]) -> dict[str, RoleSpec]:
    """Combine stored role patterns with code-defined built-in patterns.

    Stored patterns extend built-ins, but cannot override their code-defined
    exclusions. Exclusions apply after the union, preserving the built-in limits
    on destructive and bot-wide permissions.
    """
    specs: dict[str, RoleSpec] = {}
    for role in roles:
        includes = list(role.includes)
        excludes = list(role.excludes)
        builtin = BUILTIN_ROLES_BY_KEY.get(role.builtin_key or "")
        if builtin is not None:
            includes = [*builtin.includes, *includes]
            excludes = [*builtin.excludes, *excludes]
        specs[str(role.id)] = RoleSpec(
            key=str(role.id),
            includes=tuple(dict.fromkeys(includes)),
            excludes=tuple(dict.fromkeys(excludes)),
            includes_roles=tuple(str(edge) for edge in role.includes_roles),
        )
    return specs


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

    @property
    def cache(self) -> SubjectRuleCache | None:
        """The rule-set cache this service reads through, if it has one."""
        return self._cache

    async def rules_for(self, subject: Subject) -> tuple[Rule, ...]:
        """Load and expand the subject's rules; the resolver still checks expiry and scope."""
        if subject.is_bot_owner:
            # The owner short-circuits before any rule is read, so loading them
            # would be pure cost.
            return ()

        key = cache_key(subject)
        if self._cache is not None and (cached := self._cache.get(key)) is not None:
            return cached

        async with self._uow_factory() as uow:
            records = await uow.permissions.load_for_subject(
                account_id=subject.account_id,
                discord_role_ids=subject.discord_role_ids,
                guild_id=subject.guild_id,
            )
        rules = self.assemble(records, subject)
        if self._cache is not None:
            self._cache.put(key, rules, epoch=records.epoch)
        return rules

    async def allows_in(self, uow: PermissionUnitOfWork, subject: Subject, node: PermissionNode) -> bool:
        """Check one permission using the caller's transaction and current stored rules."""
        return node.name in await self.capabilities_in(uow, subject, (node,))

    async def capabilities_in(
        self, uow: PermissionUnitOfWork, subject: Subject, nodes: Iterable[PermissionNode | str]
    ) -> frozenset[str]:
        """Resolve authority from the caller's transaction without using or populating the cache."""
        if subject.is_bot_owner:
            rules: tuple[Rule, ...] = ()
        else:
            records = await uow.permissions.load_for_subject(
                account_id=subject.account_id,
                discord_role_ids=subject.discord_role_ids,
                guild_id=subject.guild_id,
            )
            rules = self.assemble(records, subject)
        return resolve_many(nodes, subject, rules, catalogue=self._catalogue)

    def assemble(self, records: SubjectRecords, subject: Subject) -> tuple[Rule, ...]:
        """Convert stored grants and role assignments into rules, including the guild-admin bridge."""
        specs = _role_specs(records.roles)
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
        subject: Subject,
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

    async def check(self, subject: Subject, node: PermissionNode | str) -> Decision:
        """Decide one node, with the trace that `/perm can` renders."""
        return resolve(node, subject, await self.rules_for(subject), catalogue=self._catalogue)

    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        """Whether `subject` holds `node`."""
        return (await self.check(subject, node)).allowed

    async def decisions(
        self,
        subject: Subject,
        nodes: Iterable[PermissionNode | str],
    ) -> tuple[Decision, ...]:
        """Decide several nodes from one rule load, retaining each decision's explanation.

        Callers can distinguish an explicit forbid from a missing grant, which a
        set of allowed capability names alone would not show.
        """
        rules = await self.rules_for(subject)
        return tuple(resolve(node, subject, rules, catalogue=self._catalogue) for node in nodes)

    async def capabilities(
        self,
        subject: Subject,
        nodes: Iterable[PermissionNode | str],
    ) -> frozenset[str]:
        """The subset of `nodes` that `subject` holds, from one load."""
        return resolve_many(nodes, subject, await self.rules_for(subject), catalogue=self._catalogue)

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
        """Return allowed capability names for adapters constructing voting or reaction actors.

        Those actors carry the resolved names so their policies can check authority
        without loading permissions themselves.
        """
        subject = Subject(
            account_id=account_id,
            discord_role_ids=frozenset(discord_role_ids),
            guild_id=guild_id,
            is_bot_owner=is_bot_owner,
            discord_guild_admin=discord_guild_admin,
        )
        return await self.capabilities(subject, nodes)

    def role_leaves(self, role: PermissionRole, roles: Iterable[PermissionRole]) -> tuple[str, ...]:
        """Return sorted node names after role composition and exclusions.

        Expansion uses the current catalogue and is not persisted.
        """
        specs = _role_specs(roles)
        expansion = expand_role(str(role.id), specs, catalogue=self._catalogue)
        leaves: set[str] = set()
        for pattern in expansion.includes:
            leaves |= self._catalogue.expand(pattern)
        return tuple(sorted(leaves - expansion.excluded))

    def is_delegable_by_guild_admin(self, pattern: str) -> bool:
        """Return whether a valid pattern selects only guild-scoped catalogue nodes.

        Malformed patterns and patterns matching no current node return False.
        """
        try:
            scopes = self._catalogue.scopes_reached(pattern)
        except InvalidPatternError:
            return False
        return bool(scopes) and scopes <= {NodeScope.GUILD}
