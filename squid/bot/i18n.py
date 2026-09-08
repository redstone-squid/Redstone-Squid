"""Discord-facing locale resolution and translation helpers."""

from typing import Any, cast, override

import discord
from discord import app_commands
from discord.ext import commands

from squid.core.i18n import DEFAULT_LOCALE, localization_for, negotiate_locale, tr
from squid.settings.application import SettingsService
from squid_ui.text import Localization, localization_scope
from squid_ui_discord.contracts import LocalizationSource
from squid_ui_discord.runtime import DiscordUIRuntime


async def resolve_locale(
    target: discord.Interaction[Any] | commands.Context[Any] | discord.Message,
    settings_service: SettingsService,
) -> str:
    """Resolve the locale to respond in.

    Chain: per-guild admin override -> the guild's Discord-reported locale -> the invoking user's
    client locale -> `DEFAULT_LOCALE`. Only interactions carry a user locale; plain messages and
    prefix commands skip that tier.
    """
    guild = target.guild
    if guild is not None:
        override = await settings_service.get_locale(guild.id)
        if override is not None:
            return negotiate_locale(override)

    # Duck-typed so the test doubles in tests/helpers/discord.py work without subclassing discord types.
    interaction = cast(Any, target if hasattr(target, "guild_locale") else getattr(target, "interaction", None))
    if interaction is not None:
        if interaction.guild_locale is not None:
            return negotiate_locale(str(interaction.guild_locale))
        return negotiate_locale(str(interaction.locale))

    if guild is not None:
        return negotiate_locale(str(guild.preferred_locale))
    return DEFAULT_LOCALE


async def localization_resolver(source: LocalizationSource) -> Localization:
    """Localization for the UI runtime, resolved through `resolve_locale` with the bot's settings service."""
    client = cast(Any, DiscordUIRuntime.of(source).client)
    target = cast(discord.Interaction[Any] | commands.Context[Any] | discord.Message, source)
    locale = await resolve_locale(target, client.services.settings)
    return localization_for(locale)


class SquidAppCommandTranslator(app_commands.Translator):
    """Translates slash command names, descriptions and choices via `squid.core.i18n.tr`.

    Returns None for the default locale so discord.py keeps the source string.
    """

    @override
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        resolved = negotiate_locale(str(locale))
        if resolved == DEFAULT_LOCALE:
            return None
        with localization_scope(localization_for(resolved)):
            return tr(string.message)
