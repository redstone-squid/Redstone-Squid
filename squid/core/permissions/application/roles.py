"""Convert one bound database snapshot into permission resolver inputs.

Database patterns and integer IDs become parsed selectors, role keys, and rules
here. Stored rows remain separate from the caller's mutable Discord membership.
"""

from collections.abc import Iterable

from squid.core.actors import Actor
from squid.core.permissions.application.ports import SubjectRecords
from squid.core.permissions.domain import (
    BUILTIN_ROLES_BY_KEY,
    BuiltinRoleKeys,
    Catalogue,
    Effect,
    Origin,
    Pattern,
    RoleGraph,
    RoleSpec,
    Rule,
    SubjectKind,
    rules_from_role,
)
from squid.core.permissions.models import PermissionGrant, PermissionRole


class PermissionRuleSnapshot:
    """Select direct, assigned, and bridge rules for the snapshot's bound subject."""

    def __init__(self, records: SubjectRecords, *, catalogue: Catalogue) -> None:
        """Convert stored grants and role assignments into rules, including the guild-admin bridge."""
        self._catalogue = catalogue
        graph = RoleGraph(self.role_specs(records.roles), catalogue=catalogue)
        rules = [self._grant_rule(grant) for grant in records.grants]
        rules.extend(self._assignment_rules(records, graph))

        bridge = self._bridge_rules(records, graph, records.subject)
        rules.extend(bridge)
        self.rules = tuple(rules)

    def _assignment_rules(self, records: SubjectRecords, graph: RoleGraph) -> list[Rule]:
        slugs = {str(role.id): role.slug for role in records.roles}
        rules: list[Rule] = []
        for assignment in records.assignments:
            key = str(assignment.role_id)
            by_account = assignment.subject_account_id is not None
            rules.extend(
                rules_from_role(
                    graph.expand(key),
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
        graph: RoleGraph,
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
                graph.expand(key),
                subject_kind=SubjectKind.DISCORD_ROLE,
                origin=Origin.GUILD_ADMIN_BRIDGE,
                scope_guild_id=subject.guild_id,
                source="discord:manage-server",
            )
        )

    @staticmethod
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

    @staticmethod
    def role_specs(roles: Iterable[PermissionRole]) -> dict[str, RoleSpec]:
        """Combine stored role patterns with code-defined built-in patterns.

        Stored patterns extend built-ins, but cannot override their code-defined
        exclusions. Exclusions apply after the union, preserving the built-in limits
        on destructive and bot-wide permissions.
        """
        specs: dict[str, RoleSpec] = {}
        for role in roles:
            includes = [Pattern.parse(pattern) for pattern in role.includes]
            excludes = [Pattern.parse(pattern) for pattern in role.excludes]
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
