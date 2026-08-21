"""Tests for the server settings panel."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import squid_layouts as sl
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.voting.domain import VoteKind
from squid_layouts.runtime.reactivity import readonly_transaction

GUILD_ID = 7
EVERYTHING = SettingsCapabilities(view_server=True, edit_server=True, edit_voting=True)


def make_guild(*, channels: dict[int, str] | None = None, roles: dict[int, str] | None = None) -> Any:
    """A guild stub exposing only the lookups the panel performs."""
    channels = channels or {}
    roles = roles or {}
    return SimpleNamespace(
        id=GUILD_ID,
        get_channel_or_thread=lambda channel_id: (
            SimpleNamespace(id=channel_id, name=channels[channel_id]) if channel_id in channels else None
        ),
        get_role=lambda role_id: SimpleNamespace(id=role_id, name=roles[role_id]) if role_id in roles else None,
    )


def make_component_panel(
    *,
    stored: dict[str, int | None] | None = None,
) -> tuple[SettingsPanel, Any]:
    """The mounted panel and the settings service behind it."""
    settings = SimpleNamespace(
        get_all=AsyncMock(return_value=stored or {}),
        get_locale=AsyncMock(return_value=None),
        set_channel=AsyncMock(),
        clear=AsyncMock(),
        set_locale=AsyncMock(),
    )
    votes = SimpleNamespace(
        emoji_preset=AsyncMock(return_value=None),
        get_role_weights=AsyncMock(return_value=()),
    )
    panel = SettingsPanel(
        settings=cast(Any, settings),
        votes=cast(Any, votes),
        guild=make_guild(channels={12: "general"}),
        author_id=1,
        capabilities=EVERYTHING,
    )
    panel.mount()
    return panel, settings


async def test_a_saved_channel_is_kept_without_a_hand_written_invalidate() -> None:
    panel, _ = make_component_panel()
    with sl.transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 12


async def test_a_channel_saved_before_a_later_failure_is_not_left_applied() -> None:
    """The guarantee the architecture doc promises, on the panel that motivated it.

    set_channel writes into a dict, which is the shape assignment-level rollback would miss.
    """
    panel, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.transaction():
        await save_the_channel_then_fail()

    assert panel.channel_id("Vote") == 3


async def test_a_half_loaded_voting_page_is_not_left_applied() -> None:
    panel, _ = make_component_panel()
    await panel.open_voting(VoteKind.BUILD)
    loaded_preset = panel._preset
    panel._votes.get_role_weights = AsyncMock(side_effect=RuntimeError("database is down"))

    with pytest.raises(RuntimeError, match="database is down"), sl.transaction():
        await panel.open_voting(VoteKind.GENERIC)

    assert panel.kind is VoteKind.BUILD
    assert panel._preset is loaded_preset


async def test_a_read_only_action_cannot_change_a_channel() -> None:
    panel, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with pytest.raises(sl.ReactiveWriteError), readonly_transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 3
