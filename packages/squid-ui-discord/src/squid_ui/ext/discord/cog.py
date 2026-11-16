"""A cog that owns a UI scope and registers its declared context menus."""

from functools import wraps
from typing import Self, cast, override

import discord
from discord import app_commands
from discord.ext import commands
from squid_ui.ext.discord.commands import AsyncHandler, bind_context_menu, context_menu_declaration
from squid_ui.ext.discord.facade import Scope
from squid_ui.ext.discord.runtime import DiscordUIRuntime

type ContextMenuTarget = discord.Message | discord.Member | discord.User


class Cog[BotT: commands.Bot](commands.Cog):
    """A Discord cog whose unload closes its UI scope.

    `@sd.command` members are ordinary discord.py commands and need nothing from the cog;
    `@sd.context_menu` methods are registered on the tree here because discord.py cannot
    hold a `ContextMenu` on a cog class. Override `cog_load` and `cog_unload` as usual;
    initialization wraps them to register menus before loading and close the scope after unloading.
    """

    def __init__(self, bot: BotT) -> None:
        super().__init__()
        self.bot = bot
        runtime = DiscordUIRuntime.of(bot)
        self.ui: Scope[Self, BotT] = runtime.scope(self)
        self._squid_context_menus: list[app_commands.ContextMenu] = []

        # Bind once per instance so super() follows the ordinary class hooks without
        # registering menus or closing the scope again at each inheritance level.
        load = self.cog_load
        unload = self.cog_unload

        @wraps(load)
        async def cog_load() -> None:
            try:
                self._register_context_menus()
                await discord.utils.maybe_coroutine(load)
            except BaseException:
                try:
                    self._remove_context_menus()
                finally:
                    await self.ui.close()
                raise

        @wraps(unload)
        async def cog_unload() -> None:
            try:
                await discord.utils.maybe_coroutine(unload)
            finally:
                try:
                    self._remove_context_menus()
                finally:
                    await self.ui.close()

        self.cog_load = cog_load
        self.cog_unload = cog_unload

    @override
    async def cog_load(self) -> None:
        """Run application setup after the declared context menus are registered."""

    @override
    async def cog_unload(self) -> None:
        """Run application teardown before menus are removed and the UI scope closes."""

    def _remove_context_menus(self) -> None:
        for menu in self._squid_context_menus:
            self.bot.tree.remove_command(menu.name, type=menu.type)
        self._squid_context_menus.clear()

    def _register_context_menus(self) -> None:
        for name in dir(type(self)):
            callback = getattr(type(self), name, None)
            if not callable(callback):
                continue
            declaration = context_menu_declaration(cast(AsyncHandler, callback))
            if declaration is None:
                continue
            menu = bind_context_menu(self, cast(AsyncHandler, callback), declaration)
            self.bot.tree.add_command(menu)
            self._squid_context_menus.append(menu)


__all__ = ["Cog", "ContextMenuTarget"]
