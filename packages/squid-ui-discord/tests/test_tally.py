"""Host-owned tally rendering and dispatch."""

from typing import cast

import pytest

import squid_ui as sl
import squid_ui_discord
import squid_ui_widgets as sp
from squid_ui import scene, testing

OPTIONS = (
    sp.TallyOption("yes", "Yes", 3, mine=True, emoji="✅"),
    sp.TallyOption("no", "No", 1),
)


def test_inert_tally_renders_counts_bars_and_reader_emphasis() -> None:
    result = sl.planning.plan(sl.tally(OPTIONS, key="vote"), target=squid_ui_discord.DISCORD_V2_DPY27)
    text = [item.content for item in testing.walk(result.scene.body) if isinstance(item, scene.Text)]

    assert any(value.startswith("Yes:") and value.endswith("75%") for value in text)
    assert any(value.startswith("No:") and value.endswith("25%") for value in text)
    assert any("✅ **Yes** — 3" in value for value in text)
    assert any("No — 1" in value for value in text)


async def test_mounted_tally_dispatches_the_selected_value() -> None:
    seen: list[sl.SelectionEvent] = []

    async def vote(event: sl.SelectionEvent) -> None:
        seen.append(event)

    result = sl.planning.plan(sl.tally(OPTIONS, key="vote", on_vote=vote), target=squid_ui_discord.DISCORD_V2_DPY27)
    select = next(item for item in testing.walk(result.scene.body) if isinstance(item, scene.Select))
    await result.bindings[select.action].handler(
        sl.SelectionEvent(
            sl.interactions.Actor("7"),
            cast(sl.interactions.ActionResponder, object()),
            context=None,
            values=("yes",),
        )
    )

    assert seen[0].values == ("yes",)


def test_mounted_tally_inherits_select_adaptation_for_many_options() -> None:
    async def vote(_event: sl.SelectionEvent) -> None: ...

    options = tuple(sp.TallyOption(str(index), f"Option {index}", index) for index in range(6))
    result = sl.planning.plan(sl.tally(options, key="vote", on_vote=vote), target=squid_ui_discord.DISCORD_V2_DPY27)

    assert any(isinstance(item, scene.Select) for item in testing.walk(result.scene.body))


def test_routed_tally_uses_one_restart_surviving_selection_route() -> None:
    result = sl.planning.plan(
        sl.tally(OPTIONS, key="vote", route_id="poll:vote"),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )
    select = next(item for item in testing.walk(result.scene.body) if isinstance(item, scene.RoutedSelect))

    assert select.route_id == "poll:vote"
    assert [option.value for option in select.options] == ["yes", "no"]


def test_tally_rejects_invalid_inputs() -> None:
    async def vote(_event: sl.SelectionEvent) -> None: ...

    with pytest.raises(ValueError, match="at least one option"):
        sl.tally((), key="vote")
    with pytest.raises(ValueError, match="on_vote or route_id"):
        sl.tally(OPTIONS, key="vote", on_vote=vote, route_id="poll:vote")
    with pytest.raises(ValueError, match="at least every option count"):
        sl.tally(OPTIONS, key="vote", total=2)
