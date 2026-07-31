"""Discord-facing locale resolution and translation helpers."""

from typing import Any, override

import discord
from discord import app_commands
from discord.ext import commands

from squid.core.i18n import DEFAULT_LOCALE, negotiate_locale, translate
from squid.settings.application import SettingsService


async def resolve_locale(
    target: "discord.Interaction[Any] | commands.Context[Any]",
    settings_service: SettingsService,
) -> str:
    """Resolve the locale to respond in for an interaction or command invocation.

    Fallback chain: per-guild admin override (`server_settings.locale`) ->
    the guild's Discord-reported locale -> the invoking user's Discord
    client locale -> `DEFAULT_LOCALE`. Prefix/text command invocations have
    no Discord-reported locale, so they fall back straight from the guild
    override to `DEFAULT_LOCALE`.
    """
    guild = target.guild
    if guild is not None:
        override = await settings_service.get_locale(guild.id)
        if override is not None:
            return negotiate_locale(override)

    # Duck-typed rather than `isinstance(target, discord.Interaction)` so lightweight
    # test doubles (see tests/helpers/discord.py) work without subclassing discord types.
    interaction = target if hasattr(target, "guild_locale") else getattr(target, "interaction", None)
    if interaction is None:
        return DEFAULT_LOCALE
    if interaction.guild_locale is not None:
        return negotiate_locale(str(interaction.guild_locale))
    return negotiate_locale(str(interaction.locale))


def t(locale: str | None, message: str, /, **params: object) -> str:
    """Translate `message` into `locale`. Thin pass-through to `squid.core.i18n.translate`."""
    return translate(locale, message, **params)


class SquidAppCommandTranslator(app_commands.Translator):
    """Translates slash command names, descriptions, and choices via `squid.core.i18n`."""

    @override
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        resolved = negotiate_locale(str(locale))
        if resolved == DEFAULT_LOCALE:
            return None  # Let discord.py fall back to the source string.
        return t(resolved, string.message)
