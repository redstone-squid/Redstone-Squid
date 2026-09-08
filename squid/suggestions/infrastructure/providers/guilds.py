"""Suggestion providers for values scoped to one Discord guild.

These require a `guild_id` in the request context. The service answers empty rather than guessing
when it is missing, so a caller that forgets the context resolver sees nothing instead of another
server's starboards.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.permissions.application.ports import RoleRecord
from squid.starboard.domain import EDITABLE_SETTINGS, StarboardConfig
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest


class GuildStarboards(Protocol):
    """Read a guild's configured starboards."""

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]:
        """Return every starboard configured in the guild, disabled ones included."""
        ...


class PermissionRoles(Protocol):
    """Read the permission roles visible from a guild."""

    async def roles(self, *, guild_id: int | None = None) -> Sequence[RoleRecord]:
        """Return the guild's own roles and the global ones, highest rank first; `None` returns all."""
        ...


class StarboardNameProvider:
    """Suggest the starboards configured in the calling guild."""

    def __init__(self, starboards: GuildStarboards) -> None:
        self._starboards = starboards

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        guild_id = int(request.context["guild_id"])
        return tuple(
            candidate(
                config.name,
                description=f"#{config.channel_id}" + ("" if config.enabled else " · disabled"),
                kind="starboard",
            )
            for config in await self._starboards.list_for_guild(guild_id)
        )


class StarboardSettingProvider:
    """Suggest the settings `/starboard edit` accepts, with the value each expects."""

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return tuple(
            candidate(name, description=_setting_hint(kind), kind="starboard_setting")
            for name, kind in sorted(EDITABLE_SETTINGS.items())
        )


class PermissionRoleProvider:
    """Suggest permission role slugs visible from the calling guild."""

    def __init__(self, roles: PermissionRoles) -> None:
        self._roles = roles

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        guild_id = int(request.context["guild_id"])
        return tuple(
            candidate(
                role.slug,
                description=f"rank {role.rank}" + (" · protected" if role.protected else ""),
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
