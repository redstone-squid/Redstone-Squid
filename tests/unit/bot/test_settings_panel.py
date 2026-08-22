"""Tests for the server settings panel."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid.bot.settings_view as settings_view
import squid_layouts as sl
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.voting.domain import VoteKind
from squid_layouts.discord.testing import commit_render
from squid_layouts.runtime.reactivity import readonly_transaction

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
        guild=make_guild(channels=channels if channels is not None else {12: "general"}),
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


async def test_changing_language_relocalizes_the_live_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, _ = make_component_panel()
    mount = panel._mount
    assert mount is not None
    commit_render(mount)
    translations = {"Server settings": "Paramètres du serveur", "Close": "Fermer"}
    monkeypatch.setattr(
        settings_view,
        "localization_for",
        lambda locale: sl.Localization(locale, gettext=lambda message: translations.get(message, message)),
    )

    with sl.transaction():
        await panel.set_locale("fr")
    view = commit_render(mount)

    text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
    labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Paramètres du serveur" in text
    assert "Fermer" in labels


async def test_a_channel_change_can_be_undone() -> None:
    panel, settings = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.transaction():
        await panel.set_channel("Vote", 12)
    assert panel.channel_id("Vote") == 12

    with sl.transaction():
        assert await panel.history.undo() is not None

    assert panel.channel_id("Vote") == 3
    # The world half went back too: the framework only ever restores the panel's own dict.
    assert settings.set_channel.await_args_list[-1].args == (GUILD_ID, "Vote", 3)


async def test_an_undone_channel_change_can_be_redone() -> None:
    panel, settings = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.transaction():
        await panel.set_channel("Vote", None)
    with sl.transaction():
        await panel.history.undo()
    with sl.transaction():
        await panel.history.redo()

    assert panel.channel_id("Vote") is None
    assert settings.clear.await_count == 2


async def test_a_failed_action_records_no_history() -> None:
    panel, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.transaction():
        await save_the_channel_then_fail()

    assert panel.history.entries == ()


async def test_the_undo_control_appears_only_once_there_is_something_to_undo() -> None:
    panel, _ = make_component_panel()
    mount = panel._mount
    assert mount is not None

    assert "Undo" not in _button_labels(commit_render(mount))
    with sl.transaction():
        await panel.set_channel("Vote", 12)
    assert "Undo" in _button_labels(commit_render(mount))


async def test_undo_is_refused_when_the_permission_was_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, settings = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with sl.transaction():
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
    monkeypatch.setattr(sl.discord, "native", lambda _event: SimpleNamespace())

    with sl.transaction():
        await panel._undo(event)

    assert settings.set_channel.await_count == writes
    assert panel.channel_id("Vote") == 12
    assert notices


async def test_a_large_guild_still_fits_one_message() -> None:
    """Five paged channel pickers used to cost 30 of the 40 components a message has."""
    panel, _ = make_component_panel(channels={index: f"channel-{index}" for index in range(1, 200)})
    await panel.open_server()
    mount = panel._mount
    assert mount is not None

    view = commit_render(mount)

    footers = [
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay) and item.content.startswith("-# Page ")
    ]
    assert len(list(view.walk_children())) <= 40
    assert len(footers) == 1  # one paged channel picker, not five


async def test_the_channel_picker_follows_the_setting_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one visible picker writes whichever setting the setting picker last named."""
    monkeypatch.setattr(settings_view, "allows", AsyncMock(return_value=True))
    monkeypatch.setattr(sl.discord, "native", lambda _event: SimpleNamespace())
    panel, settings = make_component_panel(stored={"Vote": 3}, channels={3: "vote", 12: "general"})
    await panel.open_server()

    with sl.transaction():
        await panel._editing_changed(cast(Any, SimpleNamespace(selected=("Builds",))))
    with sl.transaction():
        await panel._channel_changed(cast(Any, SimpleNamespace(selected=("12",), context={})))

    assert panel.editing == "Builds"
    assert panel.channel_id("Builds") == 12
    assert panel.channel_id("Vote") == 3
    assert settings.set_channel.await_args_list[-1].args == (GUILD_ID, "Builds", 12)


def _button_labels(view: Any) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
