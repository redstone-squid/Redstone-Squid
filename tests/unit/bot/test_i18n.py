"""Bot locale resolution and translation tests."""

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Unpack, cast

import discord
import pytest
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot.i18n import localization_for, localization_resolver, resolve_locale, t
from squid.settings.application import SettingsService
from squid.settings.domain import Setting, SettingOptions


class FakeSettingsRepository:
    def __init__(self, locale: str | None = None) -> None:
        self._locale = locale

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | None]:
        return {}

    async def get_single(self, server_id: int, setting: Setting) -> int | None:
        return None

    async def get_all(self, server_id: int) -> SettingOptions:
        return SettingOptions()

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        return None

    async def get_locale(self, server_id: int) -> str | None:
        return self._locale

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        self._locale = locale

    async def on_guild_join(self, server_id: int) -> None:
        return None

    async def on_guild_remove(self, server_id: int) -> None:
        return None


def _make_guild(guild_id: int | None, preferred_locale: discord.Locale = discord.Locale.american_english):
    if guild_id is None:
        return None
    return SimpleNamespace(id=guild_id, preferred_locale=preferred_locale)


def _make_interaction(
    *,
    guild_id: int | None = 1,
    guild_locale: discord.Locale | None = None,
    locale: discord.Locale = discord.Locale.american_english,
) -> discord.Interaction[discord.Client]:
    return cast(
        "discord.Interaction[discord.Client]",
        SimpleNamespace(
            guild=_make_guild(guild_id),
            guild_locale=guild_locale,
            locale=locale,
            interaction=None,
        ),
    )


def test_t_is_a_thin_pass_through() -> None:
    assert t("en", "Try again in {seconds:.1f} seconds.", seconds=1.0) == "Try again in 1.0 seconds."


def test_localization_for_builds_the_negotiated_catalogue(mocker: MockerFixture) -> None:
    catalog = mocker.Mock()
    catalog.gettext.return_value = "translated"
    mocker.patch("squid.bot.i18n.catalog_for", return_value=catalog)

    localization = localization_for("zh-CN")

    assert localization.locale == "zh-CN"
    assert localization.gettext("Cancel") == "translated"
    catalog.gettext.assert_called_once_with("Cancel")


@pytest.mark.asyncio
async def test_localization_resolver_uses_the_installed_bot_settings() -> None:
    class FakeClient:
        def __init__(self, services: object) -> None:
            self.services = services

    service = SettingsService(FakeSettingsRepository(locale="zh-CN"))
    client = FakeClient(SimpleNamespace(settings=service))
    runtime = sd.install(cast(discord.Client, client), localization=localization_resolver)
    context = cast(
        "object",
        SimpleNamespace(
            bot=client,
            author=SimpleNamespace(id=7),
            guild=_make_guild(1),
            interaction=None,
            send=lambda **kwargs: None,
        ),
    )

    localization = await localization_resolver(context)  # type: ignore[arg-type]
    await runtime.close()

    assert localization.locale == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_prefers_guild_override() -> None:
    service = SettingsService(FakeSettingsRepository(locale="zh-CN"))
    interaction = _make_interaction(guild_locale=discord.Locale.american_english)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_falls_back_to_guild_locale() -> None:
    service = SettingsService(FakeSettingsRepository(locale=None))
    interaction = _make_interaction(guild_locale=discord.Locale.chinese)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_falls_back_to_user_locale() -> None:
    service = SettingsService(FakeSettingsRepository(locale=None))
    interaction = _make_interaction(guild_locale=None, locale=discord.Locale.chinese)

    assert await resolve_locale(interaction, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_defaults_when_no_guild() -> None:
    service = SettingsService(FakeSettingsRepository())
    interaction = _make_interaction(guild_id=None, locale=discord.Locale.french)

    assert await resolve_locale(interaction, service) == "en"


@pytest.mark.asyncio
async def test_resolve_locale_context_without_interaction_falls_back_to_guild_preferred_locale() -> None:
    service = SettingsService(FakeSettingsRepository())
    ctx = cast(
        "object",
        SimpleNamespace(guild=_make_guild(1, preferred_locale=discord.Locale.chinese), interaction=None),
    )

    assert await resolve_locale(ctx, service) == "zh-CN"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_locale_message_falls_back_to_guild_preferred_locale() -> None:
    service = SettingsService(FakeSettingsRepository())
    message = cast(
        "discord.Message",
        SimpleNamespace(guild=_make_guild(1, preferred_locale=discord.Locale.chinese)),
    )

    assert await resolve_locale(message, service) == "zh-CN"


@pytest.mark.asyncio
async def test_resolve_locale_message_without_guild_defaults() -> None:
    service = SettingsService(FakeSettingsRepository())
    message = cast("discord.Message", SimpleNamespace(guild=None))

    assert await resolve_locale(message, service) == "en"
