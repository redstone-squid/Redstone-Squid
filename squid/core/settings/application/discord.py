"""Discord names and kinds for the IDs runtime settings hold.

Runtime settings point at channels, roles and a user in the owner guild. Editors
need their names, pickers need the guild's channels and roles, and an edit should
not store a role ID in a channel field. Discord is reached through
`DiscordDirectory`, so the same checks run in every process that has a bot token.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from squid.core.errors import ServiceUnavailableError
from squid.core.i18n import tr
from squid.core.settings.domain.runtime import RUNTIME_FIELDS, DiscordKind, RuntimeSettings
from squid.core.validation import RuleIssue, RuleParams


class ChannelType(StrEnum):
    TEXT = "text"
    ANNOUNCEMENT = "announcement"
    FORUM = "forum"
    MEDIA = "media"
    VOICE = "voice"
    STAGE = "stage"
    THREAD = "thread"
    CATEGORY = "category"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class DiscordChannel:
    id: int
    name: str
    type: ChannelType


@dataclass(frozen=True, slots=True)
class DiscordRole:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class DiscordUser:
    id: int
    name: str


class DiscordUnavailableError(ServiceUnavailableError):
    """Discord could not be reached, or this deployment has no bot token or owner guild."""

    default_message = tr(t"settings-discord-unavailable = Discord is unavailable, so names cannot be looked up.")
    default_resource = "discord"


class DiscordDirectory(Protocol):
    """Discord reads the bot can make. Each raises DiscordUnavailableError when Discord cannot answer."""

    async def guild_channels(self, guild_id: int) -> Sequence[DiscordChannel]: ...

    async def guild_roles(self, guild_id: int) -> Sequence[DiscordRole]: ...

    async def channel(self, channel_id: int) -> DiscordChannel | None:
        """Return a channel the bot can see in any guild, or None."""
        ...

    async def user(self, user_id: int) -> DiscordUser | None: ...


class RuntimeDiscordReferences:
    """Resolve and check the Discord IDs in runtime settings.

    Roles must belong to the owner guild. Channels may be in any guild the bot can
    see, because build logs are read from partner servers too.
    """

    def __init__(self, directory: DiscordDirectory | None, *, owner_guild_id: int | None) -> None:
        self._directory = directory
        self._owner_guild_id = owner_guild_id

    async def channels(self) -> Sequence[DiscordChannel]:
        """List the owner guild's channels, in Discord's order."""
        directory, guild_id = self._owner_guild()
        return await directory.guild_channels(guild_id)

    async def roles(self) -> Sequence[DiscordRole]:
        """List the owner guild's roles, in Discord's order."""
        directory, guild_id = self._owner_guild()
        return await directory.guild_roles(guild_id)

    async def names(self, settings: RuntimeSettings) -> Mapping[int, str] | None:
        """Name every Discord ID in `settings`, or return None when Discord is unavailable.

        An ID missing from the result is one Discord no longer knows, or the bot cannot see.
        """
        wanted: dict[DiscordKind, set[int]] = {kind: set() for kind in DiscordKind}
        for name, field in RUNTIME_FIELDS.items():
            if field.discord_kind is not None:
                wanted[field.discord_kind].update(settings.discord_ids(name))
        try:
            directory, _guild_id = self._owner_guild()
            names: dict[int, str] = {}
            if wanted[DiscordKind.ROLE]:
                names |= {role.id: role.name for role in await self.roles() if role.id in wanted[DiscordKind.ROLE]}
            if wanted[DiscordKind.CHANNEL]:
                # One listing covers most channels; the rest live in other guilds.
                listed = {channel.id: channel.name for channel in await self.channels()}
                for channel_id in wanted[DiscordKind.CHANNEL]:
                    if channel_id in listed:
                        names[channel_id] = listed[channel_id]
                    elif (channel := await directory.channel(channel_id)) is not None:
                        names[channel_id] = channel.name
            for user_id in wanted[DiscordKind.USER]:
                if (user := await directory.user(user_id)) is not None:
                    names[user_id] = user.name
        except DiscordUnavailableError:
            return None
        return names

    async def issues(self, before: RuntimeSettings, after: RuntimeSettings) -> tuple[RuleIssue, ...]:
        """Report newly set IDs that are not the kind their field expects.

        A channel field rejects categories, which cannot hold messages. When Discord is
        unavailable nothing is reported, so an outage never blocks an edit.
        """
        added = [
            (name, field.discord_kind, discord_id)
            for name, field in RUNTIME_FIELDS.items()
            if field.discord_kind is not None
            for discord_id in sorted(set(after.discord_ids(name)) - set(before.discord_ids(name)))
        ]
        if not added:
            return ()
        try:
            directory, _guild_id = self._owner_guild()
            roles = (
                {role.id for role in await self.roles()} if any(k is DiscordKind.ROLE for _, k, _ in added) else set()
            )
            issues: list[RuleIssue] = []
            for name, kind, discord_id in added:
                match kind:
                    case DiscordKind.ROLE:
                        reason = None if discord_id in roles else "unknown"
                    case DiscordKind.USER:
                        reason = None if await directory.user(discord_id) is not None else "unknown"
                    case DiscordKind.CHANNEL:
                        channel = await directory.channel(discord_id)
                        reason = (
                            "unknown"
                            if channel is None
                            else "category"
                            if channel.type is ChannelType.CATEGORY
                            else None
                        )
                if reason is not None:
                    issues.append(RuleIssue(field=name, params=RuleParams(rule=f"discord_{kind}", reason=reason)))
        except DiscordUnavailableError:
            return ()
        return tuple(issues)

    def _owner_guild(self) -> tuple[DiscordDirectory, int]:
        if self._directory is None or self._owner_guild_id is None:
            raise DiscordUnavailableError
        return self._directory, self._owner_guild_id
