"""Owner-scoped Cog lifecycle and context-menu composition."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Self, cast

import discord
from discord import app_commands
from discord.app_commands.commands import ContextMenuCallback
from discord.ext import commands

from squid_ui_discord.ext.commands import context_menu_declaration, present_return
from squid_ui_discord.ext.contracts import AsyncHandler, CommandResult
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.request import AcknowledgementPolicy, DiscordRequest

type ContextMenuTarget = discord.Message | discord.Member | discord.User


class Cog[BotT: commands.Bot](commands.Cog):
    """A Discord cog whose final unload hook ends its UI scope."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        forbidden = tuple(name for name in ("cog_load", "cog_unload") if name in cls.__dict__)
        if forbidden:
            names = ", ".join(forbidden)
            message = f"sdx.Cog owns {names}; override ui_load/ui_unload instead"
            raise TypeError(message)

    def __init__(self, bot: BotT) -> None:
        super().__init__()
        self.bot = bot
        runtime = getattr(bot, "ui", None)
        if runtime is None:
            message = "sdx.Cog requires a bot with an installed Discord UI runtime"
            raise TypeError(message)
        self.ui: DiscordUI[Self] = runtime.scope(self)
        self._squid_context_menus: list[app_commands.ContextMenu] = []

    async def ui_load(self) -> None:
        """Run application-specific work after facade declarations are installed."""

    async def ui_unload(self) -> None:
        """Run application-specific work before facade declarations are removed."""

    async def cog_load(self) -> None:
        """Register declarations and roll them back if application loading fails."""
        try:
            self._register_context_menus()
            await self.ui_load()
        except BaseException:
            self._remove_context_menus()
            await self.ui.close()
            raise

    async def cog_unload(self) -> None:
        """Run application teardown, unregister declarations, and close the scope."""
        try:
            await self.ui_unload()
        finally:
            try:
                self._remove_context_menus()
            finally:
                await self.ui.close()

    def _remove_context_menus(self) -> None:
        for menu in self._squid_context_menus:
            self.bot.tree.remove_command(menu.name, type=menu.type)
        self._squid_context_menus.clear()

    def _register_context_menus(self) -> None:
        def context_callback(
            callback: Callable[
                [DiscordRequest[Self, discord.Interaction[discord.Client]], ContextMenuTarget],
                Awaitable[CommandResult],
            ],
            acknowledgement: AcknowledgementPolicy,
            menu_type: discord.AppCommandType,
        ) -> Callable[[discord.Interaction[discord.Client], ContextMenuTarget], Awaitable[None]]:
            @wraps(callback)
            async def invoke(interaction: discord.Interaction[discord.Client], target: ContextMenuTarget) -> None:
                request = await DiscordRequest.create(
                    self.ui,
                    interaction,
                    acknowledgement=acknowledgement,
                )
                if acknowledgement in ("private", "public"):
                    await request.defer(acknowledgement)
                result = await callback(request, target)
                await present_return(request, result)

            target_type = discord.Message if menu_type is discord.AppCommandType.message else discord.Member
            invoke.__annotations__ = {
                "interaction": discord.Interaction,
                "target": target_type,
                "return": None,
            }
            # discord.py rejects callbacks whose qualified name still looks like an
            # unbound class method. This closure has already bound the cog owner.
            invoke.__qualname__ = invoke.__name__
            return invoke

        for name in dir(type(self)):
            callback = getattr(type(self), name, None)
            if not callable(callback):
                continue
            declaration = context_menu_declaration(cast(AsyncHandler, callback))
            if declaration is None:
                continue
            bound = cast(
                Callable[
                    [DiscordRequest[Self, discord.Interaction[discord.Client]], ContextMenuTarget],
                    Awaitable[CommandResult],
                ],
                getattr(self, name),
            )
            menu = app_commands.ContextMenu(
                name=declaration.name,
                callback=cast(
                    ContextMenuCallback,
                    context_callback(bound, declaration.acknowledgement, declaration.type),
                ),
                type=declaration.type,
            )
            self.bot.tree.add_command(menu)
            self._squid_context_menus.append(menu)


__all__ = ["Cog"]
