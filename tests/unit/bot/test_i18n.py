"""Bot locale resolution and translation tests."""

from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import discord
import pytest
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot.i18n import localization_for, localization_resolver, resolve_locale
from squid.settings.application import SettingsService
from squid.settings.models import ServerSetting
from tests.support.settings import FakeSettingsUnitOfWork


def _settings_service(locale: str | None = None) -> SettingsService:
    rows = {1: ServerSetting(server_id=1, locale=locale)}
    return SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, rows))


@dataclass(frozen=True)
class Guild:
    id: int
    preferred_locale: discord.Locale


@dataclass(frozen=True)
class InteractionSource:
    guild: Guild | None
    guild_locale: discord.Locale | None
    locale: discord.Locale
    interaction: None = None


@dataclass(frozen=True)
class Services:
    settings: SettingsService


class FakeClient:
    def __init__(self, services: Services) -> None:
        self.services = services


@dataclass(frozen=True)
class Author:
    id: int


@dataclass(frozen=True)
class ContextSource:
    bot: FakeClient
    author: Author
    guild: Guild | None
    interaction: None = None
    send: Any = None


@dataclass(frozen=True)
class MessageSource:
    guild: Guild | None


def _make_guild(guild_id: int | None, preferred_locale: discord.Locale = discord.Locale.american_english):
    if guild_id is None:
        return None
    return Guild(id=guild_id, preferred_locale=preferred_locale)


def _make_interaction(
    *,
    guild_id: int | None = 1,
    guild_locale: discord.Locale | None = None,
    locale: discord.Locale = discord.Locale.american_english,
) -> discord.Interaction[discord.Client]:
    return cast(
        "discord.Interaction[discord.Client]",
        InteractionSource(
            guild=_make_guild(guild_id),
            guild_locale=guild_locale,
            locale=locale,
            interaction=None,
        ),
    )


def test_localization_for_builds_the_negotiated_catalogue(mocker: MockerFixture) -> None:
    catalog = mocker.Mock()
    catalog.gettext.return_value = "translated"
    mocker.patch("squid.core.i18n.catalog_for", return_value=catalog)

    localization = localization_for("zh-CN")

    assert localization.locale == "zh-CN"
    assert localization.gettext("Cancel") == "translated"
    catalog.gettext.assert_called_once_with("Cancel")


@pytest.mark.asyncio
async def test_localization_resolver_uses_the_installed_bot_settings() -> None:
    service = _settings_service(locale="zh-CN")
    client = FakeClient(Services(settings=service))
    runtime = sd.install(cast(discord.Client, client), sd.Config(localization=localization_resolver))
    context = cast(
        "object",
        ContextSource(
            bot=client,
            author=Author(id=7),
            guild=_make_guild(1),
        ),
    )

    localization = await localization_resolver(context)  # type: ignore[arg-type]
    await runtime.close()

    assert localization.locale == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_prefers_guild_override() -> None:
    service = _settings_service(locale="zh-CN")
    interaction = _make_interaction(guild_locale=discord.Locale.american_english)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_falls_back_to_guild_locale() -> None:
    service = _settings_service(locale=None)
    interaction = _make_interaction(guild_locale=discord.Locale.chinese)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_falls_back_to_user_locale() -> None:
    service = _settings_service(locale=None)
    interaction = _make_interaction(guild_locale=None, locale=discord.Locale.chinese)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_defaults_when_no_guild() -> None:
    service = _settings_service()
    interaction = _make_interaction(guild_id=None, locale=discord.Locale.french)

    assert await resolve_locale(interaction, service) == "en"


@pytest.mark.asyncio
async def test_resolve_locale_context_without_interaction_falls_back_to_guild_preferred_locale() -> None:
    service = _settings_service()
    ctx = cast(
        "object",
        MessageSource(guild=_make_guild(1, preferred_locale=discord.Locale.chinese)),
    )

    assert await resolve_locale(ctx, service) == "zh-CN"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_locale_message_falls_back_to_guild_preferred_locale() -> None:
    service = _settings_service()
    message = cast(
        "discord.Message",
        MessageSource(guild=_make_guild(1, preferred_locale=discord.Locale.chinese)),
    )

    assert await resolve_locale(message, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_message_without_guild_defaults() -> None:
    service = _settings_service()
    message = cast("discord.Message", MessageSource(guild=None))

    assert await resolve_locale(message, service) == "en"
