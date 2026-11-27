"""Permission administration wire representations.

Discord IDs are decimal strings to avoid JavaScript rounding. Accounts are named by
their public ID; an account merged away since a row was written reads as null.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import groupby
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from squid.core.permissions.domain import Decision, Default, Effect, NodeScope, PermissionNode, Reason
from squid.core.permissions.domain.models import Tag
from squid.core.permissions.domain.resolution import LossReason, Origin
from squid.core.permissions.models import (
    PermissionAuditEntry,
    PermissionGrant,
    PermissionRole,
    PermissionRoleAssignment,
)

SNOWFLAKE_PATTERN = r"^[1-9][0-9]{0,19}$"
Snowflake = Annotated[str, Field(pattern=SNOWFLAKE_PATTERN)]
EffectName = Literal["allow", "deny", "forbid"]
_EFFECTS: Mapping[int, EffectName] = {
    int(Effect.ALLOW): "allow",
    int(Effect.DENY): "deny",
    int(Effect.FORBID): "forbid",
}
type AccountIds = Mapping[int, UUID]
_ORIGINS: Mapping[Origin, Literal["guild_admin_bridge", "role", "discord_role_grant", "account_grant"]] = {
    Origin.GUILD_ADMIN_BRIDGE: "guild_admin_bridge",
    Origin.ROLE: "role",
    Origin.DISCORD_ROLE_GRANT: "discord_role_grant",
    Origin.ACCOUNT_GRANT: "account_grant",
}


def _snowflake(value: int | None) -> str | None:
    return None if value is None else str(value)


class PermissionNodeDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scope: NodeScope
    description: str
    default_allowed: bool
    tags: list[Tag]


class PermissionDomainDetail(BaseModel):
    """The nodes under one first name segment, such as `build`."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    nodes: list[PermissionNodeDetail]

    @classmethod
    def catalogue(cls, nodes: Sequence[PermissionNode]) -> list[Self]:
        ordered = sorted(nodes, key=lambda node: node.name)
        return [
            cls(
                domain=domain,
                nodes=[
                    PermissionNodeDetail(
                        name=node.name,
                        scope=node.scope,
                        description=node.description.resolve(),
                        default_allowed=node.default is Default.ALLOW,
                        tags=sorted(node.tags),
                    )
                    for node in members
                ],
            )
            for domain, members in groupby(ordered, key=lambda node: node.segments[0])
        ]


class AccountSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["account"] = "account"
    account_id: UUID | None
    """Null when the account was merged away after the row was written."""


class DiscordRoleSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["discord_role"] = "discord_role"
    guild_id: Snowflake
    discord_role_id: Snowflake


type SubjectDetail = Annotated[AccountSubject | DiscordRoleSubject, Field(discriminator="kind")]


def subject_detail(row: PermissionGrant | PermissionRoleAssignment, accounts: AccountIds) -> SubjectDetail:
    if row.subject_account_id is not None:
        return AccountSubject(account_id=accounts.get(row.subject_account_id))
    return DiscordRoleSubject(guild_id=str(row.subject_guild_id), discord_role_id=str(row.subject_role_id))


class GrantDetail(BaseModel):
    """A stored allow, deny or forbid rule."""

    model_config = ConfigDict(extra="forbid")

    id: int
    subject: SubjectDetail
    pattern: str
    effect: EffectName
    scope_guild_id: str | None
    """The guild the rule applies in, or null for everywhere."""
    expires_at: datetime | None
    granted_at: datetime
    granted_by_account_id: UUID | None
    reason: str | None

    @classmethod
    def of(cls, grant: PermissionGrant, accounts: AccountIds) -> Self:
        return cls(
            id=grant.id,
            subject=subject_detail(grant, accounts),
            pattern=grant.pattern,
            effect=_EFFECTS[grant.effect],
            scope_guild_id=_snowflake(grant.scope_guild_id),
            expires_at=None if grant.expires_at is None else grant.expires_at.to_stdlib(),
            granted_at=grant.granted_at.to_stdlib(),
            granted_by_account_id=accounts.get(grant.granted_by_account_id or 0),
            reason=grant.reason,
        )


class AssignmentDetail(BaseModel):
    """A permission role given to an account or to everyone holding a Discord role."""

    model_config = ConfigDict(extra="forbid")

    id: int
    role_id: int
    subject: SubjectDetail
    scope_guild_id: str | None
    expires_at: datetime | None
    granted_at: datetime
    granted_by_account_id: UUID | None
    reason: str | None

    @classmethod
    def of(cls, assignment: PermissionRoleAssignment, accounts: AccountIds) -> Self:
        return cls(
            id=assignment.id,
            role_id=assignment.role_id,
            subject=subject_detail(assignment, accounts),
            scope_guild_id=_snowflake(assignment.scope_guild_id),
            expires_at=None if assignment.expires_at is None else assignment.expires_at.to_stdlib(),
            granted_at=assignment.granted_at.to_stdlib(),
            granted_by_account_id=accounts.get(assignment.granted_by_account_id or 0),
            reason=assignment.reason,
        )


class RoleDetail(BaseModel):
    """A bundle of patterns. Rank decides who may manage it, never which rule wins."""

    model_config = ConfigDict(extra="forbid")

    id: int
    slug: str
    name: str
    description: str | None
    guild_id: str | None
    """The guild that owns the role, or null for a global role."""
    rank: int
    builtin: bool
    protected: bool
    """Only the bot owner may restructure a protected role."""
    includes: list[str]
    excludes: list[str]
    included_role_ids: list[int]

    @classmethod
    def of(cls, role: PermissionRole) -> Self:
        return cls(
            id=role.id,
            slug=role.slug,
            name=role.name,
            description=role.description,
            guild_id=_snowflake(role.guild_id),
            rank=role.rank,
            builtin=role.builtin_key is not None,
            protected=role.protected,
            includes=list(role.includes),
            excludes=list(role.excludes),
            included_role_ids=list(role.includes_roles),
        )


class AuditEntryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    at: datetime
    action: str
    actor_account_id: UUID | None
    subject_kind: str | None
    subject_id: str | None
    """A public account ID or a Discord role ID, as `subject_kind` says."""
    subject_guild_id: str | None
    pattern: str | None
    role_id: int | None
    scope_guild_id: str | None
    effect: EffectName | None
    expires_at: datetime | None
    reason: str | None

    @classmethod
    def of(cls, entry: PermissionAuditEntry, accounts: AccountIds) -> Self:
        subject_id: str | None = _snowflake(entry.subject_id)
        if entry.subject_kind == "account" and entry.subject_id is not None:
            public = accounts.get(entry.subject_id)
            subject_id = None if public is None else str(public)
        return cls(
            id=entry.id,
            at=entry.at.to_stdlib(),
            action=entry.action,
            actor_account_id=accounts.get(entry.actor_account_id or 0),
            subject_kind=entry.subject_kind,
            subject_id=subject_id,
            subject_guild_id=_snowflake(entry.subject_guild_id),
            pattern=entry.pattern,
            role_id=entry.role_id,
            scope_guild_id=_snowflake(entry.scope_guild_id),
            effect=None if entry.effect is None else _EFFECTS.get(entry.effect),
            expires_at=None if entry.expires_at is None else entry.expires_at.to_stdlib(),
            reason=entry.reason,
        )


class RuleDetail(BaseModel):
    """One step of a decision's trace."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    effect: EffectName
    origin: Literal["guild_admin_bridge", "role", "discord_role_grant", "account_grant"]
    """How the subject holds the rule."""
    source: str
    via: str | None
    """The permission role the rule came through, if any."""
    scope_guild_id: str | None
    decisive: bool
    lost_on: LossReason | None


class DecisionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    allowed: bool
    reason: Reason
    trace: list[RuleDetail]

    @classmethod
    def of(cls, decision: Decision) -> Self:
        return cls(
            node=decision.node,
            allowed=decision.allowed,
            reason=decision.reason,
            trace=[
                RuleDetail(
                    pattern=str(step.rule.pattern),
                    effect=_EFFECTS[int(step.rule.effect)],
                    origin=_ORIGINS[step.rule.origin],
                    source=step.rule.source,
                    via=step.rule.via,
                    scope_guild_id=_snowflake(step.rule.scope_guild_id),
                    decisive=step.decisive,
                    lost_on=step.lost_on,
                )
                for step in decision.trace
            ],
        )


# Requests


class AccountSubjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["account"]
    account_id: UUID


class DiscordRoleSubjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["discord_role"]
    guild_id: Snowflake
    discord_role_id: Snowflake


type SubjectRequest = Annotated[AccountSubjectRequest | DiscordRoleSubjectRequest, Field(discriminator="kind")]


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: SubjectRequest
    pattern: str = Field(min_length=1, max_length=200)
    effect: EffectName = "allow"
    scope_guild_id: Snowflake | None = None
    expires_at: AwareDatetime | None = None
    reason: str | None = Field(default=None, max_length=500)
    """Required for `forbid`, which only the bot owner may write."""


class RoleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    guild_id: Snowflake | None = None
    rank: int = Field(default=0, ge=0)


class RoleRankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=0)


class RolePatternRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, max_length=200)
    mode: Literal["include", "exclude"] = "include"


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: int
    subject: SubjectRequest
    scope_guild_id: Snowflake | None = None
    expires_at: AwareDatetime | None = None
    reason: str | None = Field(default=None, max_length=500)
