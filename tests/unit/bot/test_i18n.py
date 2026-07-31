"""Bot locale resolution and translation tests."""

from types import SimpleNamespace
from typing import cast

import discord
import pytest

from squid.bot.i18n import resolve_locale, t
from squid.settings.application import SettingsService


class FakeSettingsRepository:
    def __init__(self, locale: str | None = None) -> None:
        self._locale = locale

    async def get_locale(self, server_id: int) -> str | None:
        return self._locale

    async def set_locale(self, server_id: int, locale: str | None) -> None:
        self._locale = locale


def _make_interaction(
    *,
    guild_id: int | None = 1,
    guild_locale: discord.Locale | None = None,
    locale: discord.Locale = discord.Locale.american_english,
) -> discord.Interaction[discord.Client]:
    return cast(
        "discord.Interaction[discord.Client]",
        SimpleNamespace(
            guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
            guild_locale=guild_locale,
            locale=locale,
            interaction=None,
        ),
    )


def test_t_is_a_thin_pass_through() -> None:
    assert t("en", "Try again in {seconds:.1f} seconds.", seconds=1.0) == "Try again in 1.0 seconds."


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
async def test_resolve_locale_context_without_interaction_defaults() -> None:
    service = SettingsService(FakeSettingsRepository())
    ctx = cast(
        "object",
        SimpleNamespace(guild=SimpleNamespace(id=1), interaction=None),
    )

    assert await resolve_locale(ctx, service) == "en"  # type: ignore[arg-type]
