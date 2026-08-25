"""Host-owned tally rendering and dispatch."""

from dataclasses import fields, is_dataclass
from typing import cast

import pytest

import squid_discord
import squid_layouts as sl
from squid_layouts.scene import SceneButton, SceneRoutedSelect, SceneSelect, SceneText

OPTIONS = (
    sl.patterns.TallyOption("yes", "Yes", 3, mine=True, emoji="✅"),
    sl.patterns.TallyOption("no", "No", 1),
)


def _walk(value: object):
    yield value
    if isinstance(value, tuple):
        for item in value:
            yield from _walk(item)
    elif is_dataclass(value):
        for item in fields(value):
            yield from _walk(getattr(value, item.name))


def test_inert_tally_renders_counts_bars_and_reader_emphasis() -> None:
    result = sl.planning.plan(sl.tally(OPTIONS, key="vote"), target=squid_discord.V2_TARGET)
    text = [item.content for item in _walk(result.scene.body) if isinstance(item, SceneText)]

    assert any(value.startswith("Yes:") and value.endswith("75%") for value in text)
    assert any(value.startswith("No:") and value.endswith("25%") for value in text)
    assert any("✅ **Yes** — 3" in value for value in text)
    assert any("No — 1" in value for value in text)


async def test_mounted_tally_inherits_button_adaptation_and_reports_one_key() -> None:
    seen: list[sl.SelectionEvent] = []

    async def vote(event: sl.SelectionEvent) -> None:
        seen.append(event)

    result = sl.planning.plan(sl.tally(OPTIONS, key="vote", on_vote=vote), target=squid_discord.V2_TARGET)
    button = next(
        item
        for item in _walk(result.scene.body)
        if isinstance(item, SceneButton) and item.label is not None and "Yes" in item.label
    )
    await result.bindings[button.action].handler(
        sl.PressEvent(sl.interactions.Actor("7"), cast(sl.interactions.ActionResponder, object()))
    )

    assert seen[0].values == ("yes",)


def test_mounted_tally_inherits_select_adaptation_for_many_options() -> None:
    async def vote(_event: sl.SelectionEvent) -> None: ...

    options = tuple(sl.patterns.TallyOption(str(index), f"Option {index}", index) for index in range(6))
    result = sl.planning.plan(sl.tally(options, key="vote", on_vote=vote), target=squid_discord.V2_TARGET)

    assert any(isinstance(item, SceneSelect) for item in _walk(result.scene.body))


def test_routed_tally_uses_one_restart_surviving_selection_route() -> None:
    result = sl.planning.plan(
        sl.tally(OPTIONS, key="vote", route_id="poll:vote"),
        target=squid_discord.V2_TARGET,
    )
    select = next(item for item in _walk(result.scene.body) if isinstance(item, SceneRoutedSelect))

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
