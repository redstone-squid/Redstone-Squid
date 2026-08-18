"""Permission application services."""

from collections.abc import Iterable

from squid.permissions.application.cache import SubjectRuleCache, cache_key
from squid.permissions.application.ports import (
    GrantRecord,
    PermissionStore,
    RoleRecord,
    SubjectRecords,
)
from squid.permissions.domain import (
    BUILTIN_ROLES_BY_KEY,
    CATALOGUE,
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

GUILD_ADMIN_KEY = "guild-admin"
"""The built-in role standing in for Discord's Manage Server permission."""


def _role_specs(roles: Iterable[RoleRecord]) -> dict[str, RoleSpec]:
    """Key stored roles by id, folding code-defined built-in patterns in.

    A built-in's pattern list lives in `squid.permissions.domain.catalogue`, and
    any rows stored against it are additive overrides on top. That is what stops
    the seeded list from freezing the catalogue as it looked on migration day.

    Stored includes cannot punch through a built-in's code-level excludes, since
    exclusions are applied after the union. That is deliberate: those excludes are
    the boundary keeping `global-admin` clear of `bot.**` and `@destructive`, so
    write access to `permission_role_patterns` must not be a route to the
    owner-only surface. Widening past the boundary needs a deploy or a new role.
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


def _grant_rule(record: GrantRecord) -> Rule:
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
        store: PermissionStore,
        *,
        catalogue: Catalogue = CATALOGUE,
        cache: SubjectRuleCache | None = None,
    ):
        self._store = store
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
        """Every rule that could bear on `subject`, role composition already applied."""
        if subject.is_bot_owner:
            # The owner short-circuits before any rule is read, so loading them
            # would be pure cost.
            return ()

        key = cache_key(subject)
        if self._cache is not None and (cached := self._cache.get(key)) is not None:
            return cached

        records = await self._store.load_for_subject(
            account_id=subject.account_id,
            discord_role_ids=subject.discord_role_ids,
            guild_id=subject.guild_id,
        )
        rules = self.assemble(records, subject)
        if self._cache is not None:
            self._cache.put(key, rules, epoch=records.epoch)
        return rules

    def assemble(self, records: SubjectRecords, subject: Subject) -> tuple[Rule, ...]:
        """Turn stored rows into resolver rules. Pure, so it is cheap to test."""
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
        """Discord's Manage Server permission, as the lowest-priority rule source.

        The permission system is evaluated first: this is a default that any
        explicit rule outranks, not a tier that competes with one.
        """
        if not subject.discord_guild_admin or subject.guild_id is None:
            return []
        key = next((str(role.id) for role in records.roles if role.builtin_key == GUILD_ADMIN_KEY), None)
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
        """Decide several nodes from one load, keeping each one's reason.

        A command that declares two nodes needs to know *why* it was refused —
        a `forbid` reads differently to a missing grant — which the capability
        set alone cannot say.
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
        """Satisfy `ActorCapabilityResolver` for contexts that must not import us.

        The voting and reactions contexts carry authorization as resolved node
        names, so they depend on this protocol rather than on this class.
        """
        subject = Subject(
            account_id=account_id,
            discord_role_ids=frozenset(discord_role_ids),
            guild_id=guild_id,
            is_bot_owner=is_bot_owner,
            discord_guild_admin=discord_guild_admin,
        )
        return await self.capabilities(subject, nodes)

    def role_leaves(self, role: RoleRecord, roles: Iterable[RoleRecord]) -> tuple[str, ...]:
        """The node names one role actually confers, sorted.

        Expansion is a read-time convenience for rendering and for the authority
        gate; nothing stores it, so a node added tomorrow appears here without a
        migration.
        """
        specs = _role_specs(roles)
        expansion = expand_role(str(role.id), specs, catalogue=self._catalogue)
        leaves: set[str] = set()
        for pattern in expansion.includes:
            leaves |= self._catalogue.expand(pattern)
        return tuple(sorted(leaves - expansion.excluded))

    def is_delegable_by_guild_admin(self, pattern: str) -> bool:
        """Whether a guild administrator may issue `pattern` inside their guild.

        True only when every catalogue leaf the pattern reaches is guild-scoped.
        A pattern reaching nothing is refused as well: it is either a typo or a
        bet on a node that does not exist yet, and both deserve an error rather
        than a grant that silently starts applying later.
        """
        try:
            scopes = self._catalogue.scopes_reached(pattern)
        except InvalidPatternError:
            return False
        return bool(scopes) and scopes <= {NodeScope.GUILD}
