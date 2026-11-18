"""The starboard extension's public recount operation."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from squid.bot.extensions import ExtensionSpec

if TYPE_CHECKING:
    import discord

    from squid.bot.starboard.debounce import EntryKey


type StarboardRecount = Callable[[discord.Message], Awaitable[tuple[EntryKey, ...]]]


@dataclass(frozen=True, slots=True)
class StarboardExports:
    recount: StarboardRecount


extension: ExtensionSpec[StarboardExports] = ExtensionSpec(id="starboard", module="squid.bot.starboard.cog")
