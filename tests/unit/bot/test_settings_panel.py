"""Tests for the server settings panel."""

from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast, override

import discord
import pytest

import squid.bot.settings_view as settings_view
import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    EditValues,
    Revision,
    RevisionConflictError,
)
from squid.permissions.domain.catalogue import SETTINGS_SERVER_EDIT
from squid.settings.application import SettingsService
from squid.settings.domain import ScalarChannelSetting, Setting, SettingOptions
from squid.settings.domain.guild import CHANNEL_FIELDS, GuildSettings
from squid.voting.application import VoteService
from squid.voting.domain import EmojiPreset, RoleWeight, VoteKind, VoteOption
from squid.voting.domain.models import VoteConfiguration
from squid_reactivity.transactions import (
    readonly_transaction,
)
from squid_ui.testing import submit_event
from squid_ui_discord.testing import commit_render, http_error, interaction_harness
from tests.support.discord import make_layout_bot
from tests.support.edits import bound_revisions

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
        self.revision = 1
        self.votes = FakeVoteService()
        self.channel_writes: list[tuple[int, ScalarChannelSetting, int]] = []
        self.clears: list[tuple[int, Setting]] = []

    @override
    async def guild_snapshot(self, guild_id: int) -> tuple[GuildSettings, int]:
        voting = {}
        for kind in VoteKind:
            weights = await self.votes.get_role_weights(guild_id, kind)
            voting[kind] = VoteConfiguration(None, tuple(weight for weight in weights if weight.kind == kind))
        return GuildSettings(
            **{name: self.stored.get(key) for key, name in CHANNEL_FIELDS.items()}, locale=self.locale, voting=voting
        ), self.revision

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
    async def configuration(self, guild_id: int, kind: VoteKind) -> VoteConfiguration:
        return VoteConfiguration(None, self.role_weights)

    @override
    async def emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset:
        return EmojiPreset(guild_id, kind, ())

    @override
    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> tuple[RoleWeight, ...]:
        del guild_id, kind
        if self.role_weight_error is not None:
            raise self.role_weight_error
        return self.role_weights

    async def set_emoji_preset(self, guild_id: int, kind: VoteKind, options: Sequence[VoteOption]) -> None:
        del guild_id, kind, options

    async def set_role_weight(self, weight: RoleWeight) -> None:
        self.role_weights = (*self.role_weights, weight)

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        self.role_weights = tuple(
            weight
            for weight in self.role_weights
            if (weight.guild_id, weight.kind, weight.role_id) != (guild_id, kind, role_id)
        )

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
    votes = settings.votes

    async def authorize(_node: object) -> bool:
        return True

    async def actor() -> Actor:
        return Actor(account_id=1, guild_id=GUILD_ID)

    async def execute(command: EditCommand, actor: Actor) -> EditResult:
        if not await panel.settings.authorize(SETTINGS_SERVER_EDIT):
            raise AuthorizationError
        if bound_revisions(command)[0].value != settings.revision:
            raise RevisionConflictError(bound_revisions(command)[0], settings.revision)
        current, _ = await settings.guild_snapshot(GUILD_ID)
        before = EditValues.of(current.model_dump(mode="json"))
        after = before.patch(command.values)
        values = after.unpack()
        for key, name in CHANNEL_FIELDS.items():
            if values[name] == getattr(current, name):
                continue
            channel = cast(int | None, values[name])
            if channel is None:
                await settings.clear(GUILD_ID, key)
            else:
                await settings.set_channel(GUILD_ID, key, channel)
        settings.locale = cast(str | None, values["locale"])
        voting = GuildSettings.model_validate(values).voting
        votes.role_weights = tuple(weight for configuration in voting.values() for weight in configuration.weights)
        settings.revision += int(before != after)
        versions = (Revision(bound_revisions(command)[0].target, settings.revision),)
        receipt = EditReceipt(command, before, after, versions)
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, versions, changed=before != after, final=False
            ),
            receipt,
        )

    panel = SettingsPanel(
        settings=settings,
        edits=cast(Any, SimpleNamespace(execute=execute)),
        actor=actor,
        guild=make_guild(channels=channels if channels is not None else {12: "general"}),
        capabilities=EVERYTHING,
        authorize=authorize,
    )
    bot = make_layout_bot()
    message_root = sd.DiscordUIRuntime.of(bot).defaults.mount(panel, access=sd.Owner(1), timeout=300)
    return panel, settings, message_root


async def test_a_saved_channel_is_kept_without_a_hand_written_invalidate() -> None:
    panel, _, _ = make_component_panel()
    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", 12)
    assert panel.server.channel_id("Vote") == 12


async def test_a_channel_saved_before_a_later_failure_is_not_left_applied() -> None:
    """Rollback must restore mutations inside the panel's channel dictionary.

    Restoring only attribute assignments would miss these changes; this checks UI state,
    not rollback of the settings service write.
    """
    panel, _, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.server.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.runtime.transaction():
        await save_the_channel_then_fail()

    assert panel.server.channel_id("Vote") == 3


async def test_a_half_loaded_voting_page_is_not_left_applied() -> None:
    panel, _, _ = make_component_panel()
    await panel.open_voting(VoteKind.BUILD)
    loaded_preset = panel.voting.configuration.preset
    votes = cast(FakeSettingsService, panel.settings._settings).votes
    votes.role_weight_error = RuntimeError("database is down")

    with pytest.raises(RuntimeError, match="database is down"), sl.runtime.transaction():
        await panel.open_voting(VoteKind.GENERIC)

    assert panel.voting.kind is VoteKind.BUILD
    assert panel.voting.configuration.preset is loaded_preset


async def test_a_read_only_action_cannot_change_a_channel() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with pytest.raises(sl.runtime.ReactiveWriteError), readonly_transaction():
        await panel.server.set_channel("Vote", 12)
    assert panel.server.channel_id("Vote") == 3
    assert settings.stored["Vote"] == 3


async def test_changing_language_relocalizes_the_live_root(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, _, message_root = make_component_panel()
    commit_render(message_root)
    original_localization = message_root.localization
    monkeypatch.setattr(
        settings_view,
        "localization_for",
        lambda locale: sl.text.Localization(
            locale, ("bot-settings-view-server-settings = Paramètres du serveur\nbot-settings-view-voting = Votes",)
        ),
    )

    with sl.runtime.transaction():
        await panel.server.set_locale("zh-CN", message_root=message_root)
    view = commit_render(message_root)

    text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
    labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Paramètres du serveur" in text
    assert "Votes" in labels

    with sl.runtime.transaction():
        assert await panel.edits.undo() is not None

    assert message_root.localization.locale == original_localization.locale


async def test_a_channel_change_can_be_undone() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", 12)
    assert panel.server.channel_id("Vote") == 12

    with sl.runtime.transaction():
        assert await panel.edits.undo() is not None

    assert panel.server.channel_id("Vote") == 3
    # Undo must compensate through the service as well as restoring the panel's dictionary.
    assert settings.channel_writes[-1] == (GUILD_ID, "Vote", 3)


async def test_a_saved_channel_change_can_be_redone() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", None)
    with sl.runtime.transaction():
        await panel.edits.undo()
    with sl.runtime.transaction():
        assert await panel.edits.redo() is not None

    assert panel.server.channel_id("Vote") is None
    assert len(settings.clears) == 2


async def test_a_failed_render_retains_the_committed_edit() -> None:
    panel, _, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()

    async def save_the_channel_then_fail() -> None:
        await panel.server.set_channel("Vote", 12)
        message = "the rest of the action failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="the rest of the action failed"), sl.runtime.transaction():
        await save_the_channel_then_fail()

    assert len(panel.edits.undoable) == 1
    with sl.runtime.transaction():
        assert await panel.edits.undo() is not None
    assert panel.server.channel_id("Vote") == 3


async def test_settings_offer_no_undo_after_a_change() -> None:
    # A setting is reversed by changing it back, so the panel keeps its rows for the settings.
    panel, _, message_root = make_component_panel()

    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", 12)
    assert not {"Undo", "Redo"} & set(_button_labels(commit_render(message_root)))


async def test_undo_is_refused_when_the_permission_was_revoked() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", 12)
    writes = len(settings.channel_writes)

    async def deny(_node: object) -> bool:
        return False

    panel.settings.authorize = deny
    with pytest.raises(AuthorizationError), sl.runtime.transaction():
        await panel.edits.undo()

    assert len(settings.channel_writes) == writes
    assert panel.server.channel_id("Vote") == 12


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
        await panel.server._channel_changed(cast(Any, Selection()), "Builds")

    assert panel.server.channel_id("Builds") == 12
    assert panel.server.channel_id("Vote") == 3
    assert settings.channel_writes[-1] == (GUILD_ID, "Builds", 12)


def _button_labels(view: Any) -> list[str | None]:
    return [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]


async def test_clearing_the_weight_form_removes_the_existing_role_weight() -> None:
    panel, _, _ = make_component_panel()
    with sl.runtime.transaction():
        await panel.voting.set_weight(42, 1.5)
    assert panel.voting.weight_for(42) == 1.5
    parsed = await settings_view._VoteWeightForm().spec().evaluate({"multiplier": ""})
    assert parsed.errors == ()
    assert parsed.values["multiplier"] is None

    with sl.runtime.transaction():
        await panel.voting._role_weight_submitted(submit_event(parsed.values), 42)

    assert panel.voting.weight_for(42) is None
    await panel.open_voting()
    assert panel.voting.weight_for(42) is None


async def test_a_reset_press_after_an_undelivered_arm_only_arms_again() -> None:
    """The reader never saw "Confirm reset", so their next press must not reset voting."""
    panel, _, message_root = make_component_panel()
    votes = cast(FakeSettingsService, panel.settings._settings).votes
    weight = RoleWeight(GUILD_ID, VoteKind.BUILD, 101, 2)
    votes.role_weights = (weight,)
    with sl.runtime.transaction():
        await panel.open_voting(VoteKind.BUILD)
    commit_render(message_root)
    reset = next(key for key in message_root.snapshot().handler_keys if key.endswith("reset"))
    generation = message_root.generation

    refused = interaction_harness(user_id=1)
    refused.response.edit_message.side_effect = http_error(message="edit refused")
    with pytest.raises(discord.HTTPException):
        await message_root.dispatch(reset, refused, generation=generation)
    await message_root.dispatch(reset, interaction_harness(user_id=1), generation=generation)

    assert votes.role_weights == (weight,)
    assert panel.voting.confirming_reset


async def test_channel_history_refuses_to_overwrite_another_writer() -> None:
    panel, settings, _ = make_component_panel(stored={"Vote": 3})
    await panel.open_server()
    with sl.runtime.transaction():
        await panel.server.set_channel("Vote", 12)
    settings.stored["Vote"] = 42
    settings.revision += 1
    with pytest.raises(RevisionConflictError), sl.runtime.transaction():
        await panel.edits.undo()
    assert settings.stored["Vote"] == 42


async def test_voting_reset_restores_weights_and_can_be_redone() -> None:
    panel, _, _ = make_component_panel()
    await panel.open_voting()
    with sl.runtime.transaction():
        await panel.voting.set_weight(99, 2)
    with sl.runtime.transaction():
        await panel.voting.reset_voting()
    assert panel.voting.weight_for(99) is None
    with sl.runtime.transaction():
        assert await panel.edits.undo() is not None
    assert panel.voting.weight_for(99) == 2
    with sl.runtime.transaction():
        assert await panel.edits.redo() is not None
    assert panel.voting.weight_for(99) is None
