"""An in-memory Discord directory for runtime settings tests."""

from collections.abc import Sequence

from squid.core.settings.application.discord import (
    ChannelType,
    DiscordChannel,
    DiscordRole,
    DiscordUnavailableError,
    DiscordUser,
)

OWNER_GUILD = 10
WELCOME = DiscordChannel(101, "welcome", ChannelType.TEXT)
LOUNGE = DiscordChannel(102, "Lounge", ChannelType.CATEGORY)
PARTNER_LOG = DiscordChannel(201, "partner-builds", ChannelType.FORUM)
REDSTONER = DiscordRole(301, "Redstoner")
STARBOARD_BOT = DiscordUser(401, "Starboard")


class FakeDirectory:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def _require(self) -> None:
        if not self.available:
            raise DiscordUnavailableError

    async def guild_channels(self, guild_id: int) -> Sequence[DiscordChannel]:
        self._require()
        return (WELCOME, LOUNGE) if guild_id == OWNER_GUILD else ()

    async def guild_roles(self, guild_id: int) -> Sequence[DiscordRole]:
        self._require()
        return (REDSTONER,) if guild_id == OWNER_GUILD else ()

    async def channel(self, channel_id: int) -> DiscordChannel | None:
        self._require()
        return next((item for item in (WELCOME, LOUNGE, PARTNER_LOG) if item.id == channel_id), None)

    async def user(self, user_id: int) -> DiscordUser | None:
        self._require()
        return STARBOARD_BOT if user_id == STARBOARD_BOT.id else None
