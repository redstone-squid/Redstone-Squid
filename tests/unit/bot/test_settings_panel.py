"""Tests for the server settings panel."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_layouts as sl
from squid.bot import settings_view
from squid.bot.settings_view import (
    FOLLOW_DISCORD,
    LocaleSelect,
    RoleWeightModal,
    SettingChannelSelect,
    SettingsCapabilities,
    SettingsPanel,
    SettingsPanelView,
)
from squid.voting.domain import EmojiPreset, RoleWeight, VoteChoice, VoteKind, VoteOption
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


def make_panel(
    *,
    capabilities: SettingsCapabilities = EVERYTHING,
    stored: dict[str, int | None] | None = None,
    locale_override: str | None = None,
    weights: tuple[RoleWeight, ...] = (),
    guild: Any = None,
) -> tuple[SettingsPanelView, Any, Any]:
    """A panel and the two service stubs behind it."""
    settings = SimpleNamespace(
        get_all=AsyncMock(return_value=stored or {}),
        get_locale=AsyncMock(return_value=locale_override),
        set_channel=AsyncMock(),
        clear=AsyncMock(),
        set_locale=AsyncMock(),
    )
    preset = EmojiPreset(
        GUILD_ID,
        VoteKind.BUILD,
        (VoteOption("👍", VoteChoice.APPROVE), VoteOption("👎", VoteChoice.DENY)),
    )
    votes = SimpleNamespace(
        emoji_preset=AsyncMock(return_value=preset),
        get_role_weights=AsyncMock(return_value=weights),
        set_role_weight=AsyncMock(),
        remove_role_weight=AsyncMock(),
        set_emoji_preset=AsyncMock(),
        reset_configuration=AsyncMock(),
    )
    view = SettingsPanelView(
        settings=cast(Any, settings),
        votes=cast(Any, votes),
        guild=guild if guild is not None else make_guild(),
        author_id=1,
        capabilities=capabilities,
    )
    return view, settings, votes


def make_interaction() -> Any:
    """An interaction stub for a component on an already-sent panel."""
    return SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild=SimpleNamespace(id=GUILD_ID),
        message=None,
        response=SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            is_done=Mock(return_value=False),
        ),
    )


@pytest.fixture(autouse=True)
def _permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grant the click-time re-check; its denial path is tested on its own."""
    monkeypatch.setattr(settings_view, "allows", AsyncMock(return_value=True))


def selects_of[SelectT](view: SettingsPanelView, kind: type[SelectT]) -> list[SelectT]:
    return [child for child in view.walk_children() if isinstance(child, kind)]


def rendered(view: SettingsPanelView) -> str:
    return str(view.to_components())


async def test_the_panel_shows_every_setting_on_one_screen() -> None:
    """The complaint phase 4 answers: seeing a guild's configuration took a command per key."""
    view, _, _ = make_panel(
        stored={"Smallest": 100, "Vote": 200},
        locale_override="zh-CN",
        guild=make_guild(channels={100: "smallest-doors", 200: "vote"}),
    )
    await view.load()

    text = rendered(view)
    assert "<#100>" in text
    assert "<#200>" in text
    assert "zh-CN" in text
    assert text.count("Not set") == 3  # Fastest, First and Builds are unconfigured.
    assert len(selects_of(view, SettingChannelSelect)) == 5
    # Components V2 allows ten top-level components; a sixth channel setting would need the
    # page split rather than another picker, and Discord would only say so at send time.
    assert len(view.to_components()) <= 10


async def test_a_channel_picker_opens_on_the_channel_it_would_replace() -> None:
    view, _, _ = make_panel(stored={"Vote": 200}, guild=make_guild(channels={200: "vote"}))
    await view.load()

    pickers = {picker.placeholder: picker for picker in selects_of(view, SettingChannelSelect)}
    vote = pickers["Builds awaiting review"]
    assert [value.id for value in vote.default_values] == [200]
    assert vote.min_values == 0, "an emptied picker is how a setting is cleared"


async def test_several_keys_change_in_one_sitting() -> None:
    """One panel, two writes, no second invocation — the point of the phase."""
    view, settings, _ = make_panel(guild=make_guild(channels={300: "builds", 400: "vote"}))
    await view.load()
    pickers = {picker.placeholder: picker for picker in selects_of(view, SettingChannelSelect)}

    for placeholder, channel_id in (("Confirmed builds", 300), ("Builds awaiting review", 400)):
        picker = pickers[placeholder]
        picker._values = [cast(Any, SimpleNamespace(id=channel_id))]
        await picker.callback(make_interaction())

    assert settings.set_channel.await_args_list[0].args == (GUILD_ID, "Builds", 300)
    assert settings.set_channel.await_args_list[1].args == (GUILD_ID, "Vote", 400)
    assert "<#300>" in rendered(view)
    assert "<#400>" in rendered(view)


async def test_emptying_a_picker_clears_that_setting() -> None:
    view, settings, _ = make_panel(stored={"Vote": 200}, guild=make_guild(channels={200: "vote"}))
    await view.load()
    picker = next(p for p in selects_of(view, SettingChannelSelect) if p.placeholder == "Builds awaiting review")

    picker._values = []
    await picker.callback(make_interaction())

    settings.clear.assert_awaited_once_with(GUILD_ID, "Vote")
    assert "<#200>" not in rendered(view)


async def test_the_language_can_be_pinned_and_handed_back() -> None:
    view, settings, _ = make_panel(locale_override="zh-CN")
    await view.load()
    picker = selects_of(view, LocaleSelect)[0]

    picker._values = ["en"]
    await picker.callback(make_interaction())
    assert settings.set_locale.await_args_list[-1].args == (GUILD_ID, "en")

    picker._values = [FOLLOW_DISCORD]
    await picker.callback(make_interaction())
    assert settings.set_locale.await_args_list[-1].args == (GUILD_ID, None)


async def test_a_channel_a_setting_names_but_the_guild_lost_says_so() -> None:
    """A stale id is a thing to fix, so the panel names it rather than hiding it."""
    view, _, _ = make_panel(stored={"Vote": 999})
    await view.load()

    assert "Not found" in rendered(view)
    assert "999" in rendered(view)


async def test_role_multipliers_are_named_rather_than_mentioned() -> None:
    """`voting show` rendered `<@&id>`, which is plain text whenever the role is uncached."""
    weights = (RoleWeight(GUILD_ID, VoteKind.BUILD, 11, 2.0), RoleWeight(GUILD_ID, VoteKind.BUILD, 22, 1.5))
    view, _, _ = make_panel(weights=weights, guild=make_guild(roles={11: "Moderator"}))
    await view.open_voting()

    text = rendered(view)
    assert "Moderator" in text
    assert "<@&11>" not in text
    assert "22" in text, "a weight on a deleted role has to stay removable"


async def test_a_blank_multiplier_removes_the_weight() -> None:
    view, _, votes = make_panel(weights=(RoleWeight(GUILD_ID, VoteKind.BUILD, 11, 2.0),))
    await view.open_voting()
    modal = RoleWeightModal(view, cast(discord.Role, SimpleNamespace(id=11, name="Moderator")))

    assert modal.multiplier.default == "2", "the modal opens on the weight it edits"
    modal.multiplier._value = "  "
    await modal.on_submit(make_interaction())

    votes.remove_role_weight.assert_awaited_once_with(GUILD_ID, VoteKind.BUILD, 11)


async def test_an_impossible_multiplier_is_refused_in_words() -> None:
    view, _, votes = make_panel()
    await view.open_voting()
    modal = RoleWeightModal(view, cast(discord.Role, SimpleNamespace(id=11, name="Moderator")))
    interaction = make_interaction()

    modal.multiplier._value = "0"
    await modal.on_submit(interaction)

    votes.set_role_weight.assert_not_awaited()
    assert "positive number" in interaction.response.send_message.await_args.args[0]


async def test_resetting_voting_asks_for_a_second_click() -> None:
    view, _, votes = make_panel()
    await view.open_voting()
    reset = next(child for child in view.walk_children() if getattr(child, "label", None) == "Reset")

    await reset.callback(make_interaction())
    votes.reset_configuration.assert_not_awaited()
    assert view.confirming_reset

    confirm = next(child for child in view.walk_children() if getattr(child, "label", None) == "Confirm reset")
    await confirm.callback(make_interaction())
    votes.reset_configuration.assert_awaited_once_with(GUILD_ID, VoteKind.BUILD)


async def test_a_reader_is_offered_no_controls_that_would_fail() -> None:
    view, _, _ = make_panel(capabilities=SettingsCapabilities(view_server=True, edit_server=False, edit_voting=False))
    await view.load()

    assert selects_of(view, SettingChannelSelect) == []
    assert selects_of(view, LocaleSelect) == []


async def test_a_vote_configurator_lands_on_the_voting_page() -> None:
    """The group admits any one of three nodes, so the panel must open on what the caller holds."""
    view, _, _ = make_panel(capabilities=SettingsCapabilities(view_server=False, edit_server=False, edit_voting=True))
    await view.load()

    text = rendered(view)
    assert "Voting" in text
    assert "Server settings" not in text


async def test_a_revoked_grant_stops_a_panel_left_open(monkeypatch: pytest.MonkeyPatch) -> None:
    view, settings, _ = make_panel()
    await view.load()
    picker = selects_of(view, SettingChannelSelect)[0]
    picker._values = [cast(Any, SimpleNamespace(id=300))]
    monkeypatch.setattr(settings_view, "allows", AsyncMock(return_value=False))
    interaction = make_interaction()

    await picker.callback(interaction)

    settings.set_channel.assert_not_awaited()
    assert interaction.response.send_message.await_count == 1


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
