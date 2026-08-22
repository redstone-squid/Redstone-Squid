"""Durable route identities and policy owned by Redstoner role controls."""

from typing import Protocol, cast

import discord

import squid_layouts as sl
from squid.bot.routes._root import routes


class _OwnerGuildClient(Protocol):
    owner_server_id: int


class OwnerGuildOnly[BotT: discord.Client](sl.discord.Middleware[BotT]):
    """Silently ignore durable role controls outside the configured owner guild."""

    async def dispatch(
        self,
        request: sl.discord.RouteRequest[BotT],
        call_next: sl.discord.RouteNext,
    ) -> None:
        interaction = request.interaction
        client = cast(_OwnerGuildClient, interaction.client)
        if interaction.guild is None or interaction.guild.id != client.owner_server_id:
            return
        await call_next()


redstoner_roles = routes.group("redstoner-roles")
redstoner_roles.add_middleware(OwnerGuildOnly())

remove_redstoner_role = redstoner_roles.define("self:remove", aliases=("remove:role:redstoner",))
