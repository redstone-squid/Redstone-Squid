"""Tests for the server settings panel."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid.bot.settings_view as settings_view
import squid_discord as sd
import squid_layouts as sl
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.voting.domain import VoteKind
from squid_discord.testing import commit_render
from squid_layouts.runtime.reactivity import readonly_transaction
from tests.helpers.discord import make_layout_bot

GUILD_ID = 7
EVERYTHING = SettingsCapabilities(view_server=True, edit_server=True, edit_voting=True)


def make_guild(*, channels: dict[int, str] | None = None, roles: dict[int, str] | None = None) -> Any:
    """A guild stub exposing only the lookups the panel performs."""
    channels = channels or {}
    roles = roles or {}
    return SimpleNamespace(
        id=GUILD_ID,
        channels=[
            SimpleNamespace(id=channel_id, name=name, type=discord.ChannelType.text)
            for channel_id, name in channels.items()
        ],
        get_channel_or_thread=lambda channel_id: (
            SimpleNamespace(id=channel_id, name=channels[channel_id]) if channel_id in channels else None
        ),
        get_role=lambda role_id: SimpleNamespace(id=role_id, name=roles[role_id]) if role_id in roles else None,
    )


def make_component_panel(
    *,
    stored: dict[str, int | None] | None = None,
    channels: dict[int, str] | None = None,
) -> tuple[SettingsPanel, Any, sd.Mount]:
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
        guild=make_guild(channels=channels if channels is not None else {12: "general"}),
        capabilities=EVERYTHING,
    )
    bot = make_layout_bot()
    mount = sd.LayoutHost.of(bot).defaults.mount(panel, access=sd.Owner(1), timeout=300)
    return panel, settings, mount


async def test_a_saved_channel_is_kept_without_a_hand_written_invalidate() -> None:
    panel, _, _ = make_component_panel()
    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 12


async def test_a_channel_saved_before_a_later_failure_is_not_left_applied() -> None:
    """The guarantee the architecture doc promises, on the panel that motivated it.

    set_channel writes into a dict, which is the shape assignment-level rollback would miss.
    """
    panel, _, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.runtime.transaction():
        await save_the_channel_then_fail()

    assert panel.channel_id("Vote") == 3


async def test_a_half_loaded_voting_page_is_not_left_applied() -> None:
    panel, _, _ = make_component_panel()
    await panel.open_voting(VoteKind.BUILD)
    loaded_preset = panel._preset
    panel._votes.get_role_weights = AsyncMock(side_effect=RuntimeError("database is down"))

    with pytest.raises(RuntimeError, match="database is down"), sl.runtime.transaction():
        await panel.open_voting(VoteKind.GENERIC)

    assert panel.kind is VoteKind.BUILD
    assert panel._preset is loaded_preset


async def test_a_read_only_action_cannot_change_a_channel() -> None:
    panel, _, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with pytest.raises(sl.runtime.ReactiveWriteError), readonly_transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 3


async def test_changing_language_relocalizes_the_live_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, _, mount = make_component_panel()
    commit_render(mount)
    original_localization = mount.localization
    translations = {"Server settings": "Paramètres du serveur", "Close": "Fermer"}
    monkeypatch.setattr(
        settings_view,
        "localization_for",
        lambda locale: sl.text.Localization(locale, gettext=lambda message: translations.get(message, message)),
    )

    with sl.runtime.transaction():
        await panel.set_locale("fr", mount=mount)
    view = commit_render(mount)

    text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
    labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Paramètres du serveur" in text
    assert "Fermer" in labels

    with sl.runtime.transaction():
        result = await panel.history.undo()

    assert result.applied
    assert mount.localization.locale == original_localization.locale


async def test_a_channel_change_can_be_undone() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 12

    with sl.runtime.transaction():
        assert await panel.history.undo() is not None

    assert panel.channel_id("Vote") == 3
    # The world half went back too: the framework only ever restores the panel's own dict.
    assert settings.set_channel.await_args_list[-1].args == (GUILD_ID, "Vote", 3)


async def test_an_effectful_undone_channel_change_is_not_falsely_redoable() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.runtime.transaction():
        await panel.set_channel("Vote", None)
    with sl.runtime.transaction():
        await panel.history.undo()
    with sl.runtime.transaction():
        result = await panel.history.redo()

    assert result.status is sl.runtime.HistoryResultStatus.EMPTY
    assert panel.channel_id("Vote") == 3
    assert settings.clear.await_count == 1


async def test_a_failed_action_records_no_history() -> None:
    panel, _, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.runtime.transaction():
        await save_the_channel_then_fail()

    assert panel.history.entries == ()


async def test_the_undo_control_appears_only_once_there_is_something_to_undo() -> None:
    panel, _, mount = make_component_panel()

    assert "Undo" not in _button_labels(commit_render(mount))
    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    assert "Undo" in _button_labels(commit_render(mount))


async def test_undo_is_refused_when_the_permission_was_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    writes = settings.set_channel.await_count

    monkeypatch.setattr(settings_view, "allows", AsyncMock(return_value=False))
    notices: list[Any] = []
    event = cast(
        Any,
        SimpleNamespace(
            responder=SimpleNamespace(),
            notice=AsyncMock(side_effect=lambda text, **kwargs: notices.append(text)),
            context={"frontend": "discord"},
        ),
    )
    monkeypatch.setattr(sd, "native", lambda _event: SimpleNamespace())

    with sl.runtime.transaction():
        await panel._undo(event)

    assert settings.set_channel.await_count == writes
    assert panel.channel_id("Vote") == 12
    assert notices


async def test_a_large_guild_still_fits_one_message() -> None:
    """Five native channel pickers cost ten V2 components regardless of guild size."""
    panel, _, mount = make_component_panel(channels={index: f"channel-{index}" for index in range(1, 200)})
    await panel.open_server()

    view = commit_render(mount)

    assert len(list(view.walk_children())) <= 40
    assert len([item for item in view.walk_children() if isinstance(item, discord.ui.ChannelSelect)]) == 5


async def test_each_channel_picker_writes_its_own_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_view, "allows", AsyncMock(return_value=True))
    monkeypatch.setattr(sd, "native", lambda _event: SimpleNamespace())
    panel, settings, _ = make_component_panel(stored={"Vote": 3}, channels={3: "vote", 12: "general"})
    await panel.open_server()

    with sl.runtime.transaction():
        await panel._channel_changed(
            cast(
                Any,
                SimpleNamespace(
                    selected=(sl.entity.EntityRef(sl.entity.EntityKind.CHANNEL, 12),),
                    context={},
                ),
            ),
            "Builds",
        )

    assert panel.channel_id("Builds") == 12
    assert panel.channel_id("Vote") == 3
    assert settings.set_channel.await_args_list[-1].args == (GUILD_ID, "Builds", 12)


def _button_labels(view: Any) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
