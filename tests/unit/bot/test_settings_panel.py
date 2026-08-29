"""Tests for the server settings panel."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast, override

import discord
import pytest

import squid.bot.settings_view as settings_view
import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.settings.application import SettingsService
from squid.settings.domain import ScalarChannelSetting, Setting, SettingOptions
from squid.voting.application import VoteService
from squid.voting.domain import EmojiPreset, RoleWeight, VoteKind, VoteOption
from squid_ui.runtime.reactivity import readonly_transaction
from squid_ui_discord.testing import commit_render
from tests.helpers.discord import make_layout_bot

GUILD_ID = 7
EVERYTHING = SettingsCapabilities(view_server=True, edit_server=True, edit_voting=True)


@dataclass(slots=True)
class FakeChannel:
    id: int
    name: str
    type: discord.ChannelType = discord.ChannelType.text


@dataclass(slots=True)
class FakeRole:
    id: int
    name: str


class FakeGuild:
    def __init__(self, channels: dict[int, str], roles: dict[int, str]) -> None:
        self.id = GUILD_ID
        self.channels = [FakeChannel(channel_id, name) for channel_id, name in channels.items()]
        self._channels = channels
        self._roles = roles

    def get_channel_or_thread(self, channel_id: int) -> FakeChannel | None:
        name = self._channels.get(channel_id)
        return None if name is None else FakeChannel(channel_id, name)

    def get_role(self, role_id: int) -> FakeRole | None:
        name = self._roles.get(role_id)
        return None if name is None else FakeRole(role_id, name)


class FakeSettingsService(SettingsService):
    def __init__(self, stored: dict[str, int | None] | None = None) -> None:
        self.stored = dict(stored or {})
        self.locale: str | None = None
        self.channel_writes: list[tuple[int, ScalarChannelSetting, int]] = []
        self.clears: list[tuple[int, Setting]] = []

    @override
    async def get_all(self, server_id: int) -> SettingOptions:
        del server_id
        return cast(SettingOptions, dict(self.stored))

    @override
    async def set_channel(self, server_id: int, setting: ScalarChannelSetting, channel_id: int) -> None:
        self.channel_writes.append((server_id, setting, channel_id))
        self.stored[setting] = channel_id

    @override
    async def clear(self, server_id: int, setting: Setting) -> None:
        self.clears.append((server_id, setting))
        self.stored.pop(setting, None)

    @override
    async def get_locale(self, server_id: int) -> str | None:
        del server_id
        return self.locale

    @override
    async def set_locale(self, server_id: int, locale: str | None) -> None:
        del server_id
        self.locale = locale


class FakeVoteService(VoteService):
    def __init__(self) -> None:
        self.role_weights: tuple[RoleWeight, ...] = ()
        self.role_weight_error: RuntimeError | None = None

    @override
    async def emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset:
        return EmojiPreset(guild_id, kind, ())

    @override
    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> tuple[RoleWeight, ...]:
        del guild_id, kind
        if self.role_weight_error is not None:
            raise self.role_weight_error
        return self.role_weights

    @override
    async def set_emoji_preset(self, guild_id: int, kind: VoteKind, options: Sequence[VoteOption]) -> None:
        del guild_id, kind, options

    @override
    async def set_role_weight(self, weight: RoleWeight) -> None:
        self.role_weights = (*self.role_weights, weight)

    @override
    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        self.role_weights = tuple(
            weight
            for weight in self.role_weights
            if (weight.guild_id, weight.kind, weight.role_id) != (guild_id, kind, role_id)
        )

    @override
    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        del guild_id, kind
        self.role_weights = ()


def make_guild(*, channels: dict[int, str] | None = None, roles: dict[int, str] | None = None) -> Any:
    """A guild stub exposing only the lookups the panel performs."""
    channels = channels or {}
    roles = roles or {}
    return FakeGuild(channels, roles)


def make_component_panel(
    *,
    stored: dict[str, int | None] | None = None,
    channels: dict[int, str] | None = None,
) -> tuple[SettingsPanel, FakeSettingsService, sd.MessageRoot]:
    """The mounted panel and the settings service behind it."""
    settings = FakeSettingsService(stored)
    votes = FakeVoteService()

    async def authorize(_node: object) -> bool:
        return True

    panel = SettingsPanel(
        settings=settings,
        votes=votes,
        guild=make_guild(channels=channels if channels is not None else {12: "general"}),
        capabilities=EVERYTHING,
        authorize=authorize,
    )
    bot = make_layout_bot()
    message_root = sd.ClientRuntime.of(bot).defaults.mount(panel, access=sd.Owner(1), timeout=300)
    return panel, settings, message_root


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
    votes = cast(FakeVoteService, panel._votes)
    votes.role_weight_error = RuntimeError("database is down")

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


async def test_changing_language_relocalizes_the_live_root(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, _, message_root = make_component_panel()
    commit_render(message_root)
    original_localization = message_root.localization
    translations = {"Server settings": "Paramètres du serveur", "Close": "Fermer"}
    monkeypatch.setattr(
        settings_view,
        "localization_for",
        lambda locale: sl.text.Localization(locale, gettext=lambda message: translations.get(message, message)),
    )

    with sl.runtime.transaction():
        await panel.set_locale("fr", message_root=message_root)
    view = commit_render(message_root)

    text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
    labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Paramètres du serveur" in text
    assert "Fermer" in labels

    with sl.runtime.transaction():
        result = await panel.history.undo()

    assert result.applied
    assert message_root.localization.locale == original_localization.locale


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
    assert settings.channel_writes[-1] == (GUILD_ID, "Vote", 3)


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
    assert len(settings.clears) == 1


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
    panel, _, message_root = make_component_panel()

    assert "Undo" not in _button_labels(commit_render(message_root))
    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    assert "Undo" in _button_labels(commit_render(message_root))


async def test_undo_is_refused_when_the_permission_was_revoked() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with sl.runtime.transaction():
        await panel.set_channel("Vote", 12)
    writes = len(settings.channel_writes)

    async def deny(_node: object) -> bool:
        return False

    panel._authorize = deny
    notices: list[Any] = []

    class Event:
        responder = object()
        context = {"frontend": "discord"}

        async def notice(self, text: object, **_kwargs: object) -> None:
            notices.append(text)

    event = cast(Any, Event())
    with sl.runtime.transaction():
        await panel._undo(event)

    assert len(settings.channel_writes) == writes
    assert panel.channel_id("Vote") == 12
    assert notices


async def test_a_large_guild_still_fits_one_message() -> None:
    """Five native channel pickers cost ten V2 components regardless of guild size."""
    panel, _, message_root = make_component_panel(channels={index: f"channel-{index}" for index in range(1, 200)})
    await panel.open_server()

    view = commit_render(message_root)

    assert len(list(view.walk_children())) <= 40
    assert len([item for item in view.walk_children() if isinstance(item, discord.ui.ChannelSelect)]) == 5


async def test_each_channel_picker_writes_its_own_setting() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3}, channels={3: "vote", 12: "general"})
    await panel.open_server()

    class Selection:
        selected = (sl.entity.EntityRef(sl.entity.EntityKind.CONVERSATION, 12),)
        context: dict[str, object] = {}

    with sl.runtime.transaction():
        await panel._channel_changed(cast(Any, Selection()), "Builds")

    assert panel.channel_id("Builds") == 12
    assert panel.channel_id("Vote") == 3
    assert settings.channel_writes[-1] == (GUILD_ID, "Builds", 12)


def _button_labels(view: Any) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
