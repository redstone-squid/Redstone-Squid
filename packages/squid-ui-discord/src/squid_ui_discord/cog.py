"""A cog that owns a UI scope and registers its declared context menus."""

from typing import Self, cast

import discord
from discord import app_commands
from discord.ext import commands

from squid_ui_discord.commands import AsyncHandler, bind_context_menu, context_menu_declaration
from squid_ui_discord.facade import Scope

type ContextMenuTarget = discord.Message | discord.Member | discord.User


class Cog[BotT: commands.Bot](commands.Cog):
    """A Discord cog owning the UI scope `self.ui`, which `cog_unload` closes.

    `@sd.command` members are ordinary discord.py commands and need nothing from the cog;
    `@sd.context_menu` methods are registered on the tree here because discord.py cannot
    hold a `ContextMenu` on a cog class. Subclasses override `ui_load` and `ui_unload`, not
    `cog_load` and `cog_unload`.

    Raises:
        TypeError: A subclass defines `cog_load` or `cog_unload`, or `bot.ui` does not hold the
            `DiscordUIRuntime` that `sd.install` returned.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        forbidden = tuple(name for name in ("cog_load", "cog_unload") if name in cls.__dict__)
        if forbidden:
            names = ", ".join(forbidden)
            message = f"sd.Cog owns {names}; override ui_load/ui_unload instead"
            raise TypeError(message)

    def __init__(self, bot: BotT) -> None:
        super().__init__()
        self.bot = bot
        runtime = getattr(bot, "ui", None)
        if runtime is None:
            message = "sd.Cog requires a bot with an installed Discord UI runtime"
            raise TypeError(message)
        self.ui: Scope[Self] = runtime.scope(self)
        self._squid_context_menus: list[app_commands.ContextMenu] = []

    async def ui_load(self) -> None:
        """Hook run after the context menus are on the tree; raising unregisters them and closes `ui`."""

    async def ui_unload(self) -> None:
        """Hook run before the context menus leave the tree; they are removed and `ui` closed even if it raises."""

    async def cog_load(self) -> None:
        """Register declarations and roll them back if application loading fails.

        Raises `app_commands.CommandAlreadyRegistered` when the tree already holds a context
        menu of the same name and type.
        """
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
