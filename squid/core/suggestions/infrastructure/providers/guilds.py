"""Suggest guild starboards, permission roles, and editable starboard settings.

Starboard names and permission roles require `guild_id` in the request context.
The transport must supply an authorized guild; the service checks that the key
exists but does not verify guild membership.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.core.permissions.models import PermissionRole
from squid.core.starboard.domain import EDITABLE_SETTINGS
from squid.core.starboard.models import StarboardConfig
from squid.core.suggestions.application import Candidate
from squid.core.suggestions.application.registry import SuggestionSource
from squid.core.suggestions.domain import SourceKind, SuggestionRequest, ValueType
from squid.core.suggestions.infrastructure.providers.static import StaticProvider


class GuildStarboards(Protocol):
    """Read a guild's configured starboards."""

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]: ...


class PermissionRoles(Protocol):
    """Read the permission roles visible from a guild."""

    async def roles(self, *, guild_id: int | None = None) -> Sequence[PermissionRole]: ...


class StarboardNameProvider:
    """Suggest the starboards configured in the calling guild."""

    def __init__(self, starboards: GuildStarboards) -> None:
        self._starboards = starboards

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        guild_id = int(request.context["guild_id"])
        return tuple(
            Candidate.create(
                config.name,
                description=f"#{config.channel_id}" + ("" if config.enabled else " · disabled"),
                kind="starboard",
            )
            for config in await self._starboards.list_for_guild(guild_id)
        )


def starboard_setting_candidates() -> tuple[Candidate, ...]:
    """Suggest editable settings with the value each expects."""
    return tuple(
        Candidate.create(name, description=_setting_hint(kind), kind="starboard_setting")
        for name, kind in sorted(EDITABLE_SETTINGS.items())
    )


class PermissionRoleProvider:
    """Suggest permission role slugs visible from the calling guild."""

    def __init__(self, roles: PermissionRoles, *, identities: bool = False) -> None:
        self._roles = roles
        self._identities = identities

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        guild_id = int(request.context["guild_id"])
        return tuple(
            Candidate.create(
                str(role.id) if self._identities else role.slug,
                label=role.name or role.slug,
                description=f"{role.slug} · rank {role.rank} · "
                + ("global" if role.guild_id is None else "this server"),
                terms=(role.name, role.slug),
                kind="permission_role",
            )
            for role in await self._roles.roles(guild_id=guild_id)
        )


def _setting_hint(kind: str) -> str:
    return {
        "boolean": "true or false",
        "threshold": "a number of reactions",
        "integer": "a whole number",
        "text": "free text",
    }[kind]


def guild_sources(
    starboards: GuildStarboards | None,
    permission_roles: PermissionRoles | None,
) -> tuple[SuggestionSource, ...]:
    sources: list[SuggestionSource] = [
        SuggestionSource(
            id="starboard_settings",
            provider=StaticProvider(starboard_setting_candidates()),
            kind=SourceKind.ENUMERABLE,
            kind_label="starboard_setting",
        )
    ]
    if starboards is not None:
        sources.append(
            SuggestionSource(
                id="starboard_names",
                provider=StarboardNameProvider(starboards),
                context_keys=frozenset({"guild_id"}),
                kind_label="starboard",
            )
        )
    if permission_roles is not None:
        sources.append(
            SuggestionSource(
                id="permission_roles",
                provider=PermissionRoleProvider(permission_roles),
                context_keys=frozenset({"guild_id"}),
                kind_label="permission_role",
            )
        )
        sources.append(
            SuggestionSource(
                id="permission_role_ids",
                provider=PermissionRoleProvider(permission_roles, identities=True),
                context_keys=frozenset({"guild_id"}),
                kind=SourceKind.ENUMERABLE,
                value_type=ValueType.INTEGER,
                kind_label="permission_role",
            )
        )
    return tuple(sources)
