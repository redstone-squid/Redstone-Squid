"""Discord-facing locale resolution and translation helpers."""

from typing import Any, cast, override

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.command_texts import AUTOMATIC_MESSAGES
from squid.core.i18n import DEFAULT_LOCALE, localization_for, negotiate_locale
from squid.settings.application import SettingsService
from squid_i18n import Localization, Markup, Message, localization_scope, source_text
from squid_ui_discord.contracts import LocalizationSource
from squid_ui_discord.runtime import DiscordUIRuntime


async def resolve_locale(
    target: discord.Interaction[Any] | commands.Context[Any] | discord.Message,
    settings_service: SettingsService,
) -> str:
    """Resolve the locale to respond in for an interaction, command, or message.

    Fallback chain: per-guild admin override (`server_settings.locale`) ->
    the guild's Discord-reported locale -> the invoking user's Discord
    client locale (interactions only) -> `DEFAULT_LOCALE`. Plain messages and
    prefix/text command invocations have no user-locale tier, since neither
    carries the author's Discord client locale.
    """
    guild = target.guild
    if guild is not None:
        override = await settings_service.get_locale(guild.id)
        if override is not None:
            return negotiate_locale(override)

    # Duck-typed rather than `isinstance(target, discord.Interaction)` so lightweight
    # test doubles (see tests/helpers/discord.py) work without subclassing discord types.
    interaction = cast(Any, target if hasattr(target, "guild_locale") else getattr(target, "interaction", None))
    if interaction is not None:
        if interaction.guild_locale is not None:
            return negotiate_locale(str(interaction.guild_locale))
        return negotiate_locale(str(interaction.locale))

    if guild is not None:
        return negotiate_locale(str(guild.preferred_locale))
    return DEFAULT_LOCALE


async def localization_resolver(source: LocalizationSource) -> Localization:
    """Resolve one installed bot invocation into its render-time localization."""
    client = cast(Any, DiscordUIRuntime.of(source).client)
    target = cast(discord.Interaction[Any] | commands.Context[Any] | discord.Message, source)
    locale = await resolve_locale(target, client.services.settings)
    return localization_for(locale)


def to_locale_str(message: Message) -> app_commands.locale_str:
    """Attach a parameter-free Fluent declaration to Discord command metadata."""
    if message.declaration is None or message.bindings:
        detail = "Discord command metadata requires a parameter-free Fluent declaration"
        raise ValueError(detail)
    return app_commands.locale_str(source_text(message, markup=Markup.PLAIN), fluent_message=message)


def bind_command_messages(
    command: app_commands.Command[Any, ..., Any] | app_commands.Group | app_commands.ContextMenu,
) -> None:
    """Attach declarations to metadata discord.py derived from signatures and docstrings.

    New or changed automatic metadata needs a declaration in command_texts.py. This
    check runs after extensions load, before any command tree can be synchronized.
    Explicit to_locale_str() declarations already carry their message.
    """

    def bind(kind: str, value: str | app_commands.locale_str | None) -> None:
        if not isinstance(value, app_commands.locale_str) or "fluent_message" in value.extras:
            return
        message = AUTOMATIC_MESSAGES.get((command.qualified_name, kind))
        if message is None or source_text(message, markup=Markup.PLAIN) != value.message:
            detail = f"Missing or stale command declaration: {command.qualified_name} {kind}"
            raise ValueError(detail)
        value.extras["fluent_message"] = message

    bind("name", command._locale_name)
    if isinstance(command, app_commands.Command | app_commands.Group):
        bind("description", command._locale_description)
    if isinstance(command, app_commands.Command):
        for parameter in command._params.values():
            bind(f"parameter-{parameter.name}-name", parameter._rename)
            bind(f"parameter-{parameter.name}-description", parameter.description)
            for choice in parameter.choices or []:
                bind(f"choice-{parameter.name}-{choice.value}", choice._locale_name)
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            bind_command_messages(child)


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
        with localization_scope(localization_for(resolved)):
            message = string.extras.get("fluent_message")
            if not isinstance(message, Message):
                return None
            return message.resolve()
