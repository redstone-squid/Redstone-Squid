"""Page-number forms use the latest pager without bypassing normal dispatch."""

from dataclasses import replace
from typing import override

import discord
import pytest

import squid_ui as sl
from squid_ui import testing as engine
from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.forms import IntField
from squid_ui.pages import PagePosition
from squid_ui.planning import PlanCache, plan
from squid_ui.planning.navigation import NavigationContext, materialized_navigation_state, page_jump_nav
from squid_ui.primitives import Lines, Paginate, Row, Text
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Everyone, MessageRoot, Owner
from squid_ui_discord.testing import commit_classic_render, commit_render, interaction_harness


class JumpPages(sl.Component[sl.DiscordTarget]):
    count: int = sl.state(100)
    visible: bool = sl.state(True)
    page: int = sl.state(0)

    def __init__(self, *, controlled: bool = False) -> None:
        self.controlled = controlled
        self.turns: list[int] = []

    async def turn(self, event: sl.PageEvent) -> None:
        self.turns.append(event.page)
        self.page = event.page

    @override
    def render(self):
        if not self.visible:
            return Text("No results")
        return Lines(
            tuple(f"Entry {index}" for index in range(self.count)),
            overflow=Paginate(
                key="entries",
                per=1,
                page=sl.controlled(self.page, self.turn) if self.controlled else sl.uncontrolled(0),
            ),
        )


async def open_jump(root: MessageRoot, key: str = "__cursor_seek.entries.jump") -> discord.ui.Modal:
    interaction = interaction_harness(components_v2=root.target != DISCORD_V1_DPY27)
    await root.dispatch(key, interaction)
    return interaction.response.send_modal.await_args.args[0]


def page_input(modal: discord.ui.Modal) -> discord.ui.TextInput:
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    field = label.component
    assert isinstance(field, discord.ui.TextInput)
    return field


@pytest.mark.parametrize("target", [DISCORD_V1_DPY27, DISCORD_V2_DPY27])
@pytest.mark.parametrize("controlled", [False, True])
async def test_jump_opens_and_updates_any_page(target, controlled: bool) -> None:
    component = JumpPages(controlled=controlled)
    root = MessageRoot(component, access=Everyone(), timeout=None, nav=page_jump_nav, target=target)
    if target == DISCORD_V1_DPY27:
        commit_classic_render(root)
    else:
        commit_render(root)
    for number in (73, 100, 1, 1):
        modal = await open_jump(root)
        field = page_input(modal)
        assert field.default is not None
        assert (
            int(field.default)
            == (component.page if controlled else root.presentation.cursor("entries").position.page) + 1
        )
        field._value = str(number)
        submitted = interaction_harness(components_v2=target != DISCORD_V1_DPY27)
        await modal.on_submit(submitted)
        if controlled:
            assert component.page == number - 1
            assert component.turns[-1] == number - 1
        else:
            assert root.presentation.cursor("entries").position.page == number - 1
        assert submitted.response.is_done()


@pytest.mark.parametrize("value", ["", "no", "1.5", "0", "-1", "101"])
async def test_invalid_page_offers_private_retry_without_moving(value: str) -> None:
    root = MessageRoot(JumpPages(), access=Everyone(), timeout=None, nav=page_jump_nav)
    commit_render(root)
    modal = await open_jump(root)
    page_input(modal)._value = value
    submitted = interaction_harness()
    await modal.on_submit(submitted)
    assert root.presentation.cursor("entries").position.page == 0
    reply = submitted.response.send_message.await_args.kwargs
    assert reply["ephemeral"]
    retry = next(item for item in reply["view"].walk_children() if isinstance(item, discord.ui.Button))
    interaction = interaction_harness()
    await retry.callback(interaction)
    reopened = interaction.response.send_modal.await_args.args[0]
    assert page_input(reopened).default == value
    page_input(reopened)._value = "47"
    await reopened.on_submit(interaction_harness())
    assert root.presentation.cursor("entries").position.page == 46


async def test_submission_uses_refreshed_range_and_retry_survives_removed_pager() -> None:
    component = JumpPages()
    root = MessageRoot(component, access=Everyone(), timeout=None, nav=page_jump_nav)
    commit_render(root)
    modal = await open_jump(root)
    component.count = 10
    commit_render(root)
    page_input(modal)._value = "73"
    submitted = interaction_harness()
    await modal.on_submit(submitted)
    assert root.presentation.cursor("entries").position.page == 0
    reply = submitted.response.send_message.await_args.kwargs
    retry = next(item for item in reply["view"].walk_children() if isinstance(item, discord.ui.Button))
    component.visible = False
    commit_render(root)
    interaction = interaction_harness()
    await retry.callback(interaction)
    reopened = interaction.response.send_modal.await_args.args[0]
    page_input(reopened)._value = "3"
    unavailable = interaction_harness()
    await reopened.on_submit(unavailable)
    notice = unavailable.response.send_message.await_args.kwargs["view"]
    assert any(
        "no longer available" in item.content
        for item in notice.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


async def test_refreshed_form_accepts_pages_added_after_opening() -> None:
    component = JumpPages()
    component.count = 10
    root = MessageRoot(component, access=Everyone(), timeout=None, nav=page_jump_nav)
    commit_render(root)
    modal = await open_jump(root)
    component.count = 100
    commit_render(root)
    page_input(modal)._value = "73"
    await modal.on_submit(interaction_harness())
    assert root.presentation.cursor("entries").position.page == 72


async def test_submission_still_checks_access() -> None:
    root = MessageRoot(JumpPages(), access=Everyone(), timeout=None, nav=page_jump_nav)
    commit_render(root)
    modal = await open_jump(root)
    root.access = Owner(999999)
    page_input(modal)._value = "73"
    await modal.on_submit(interaction_harness())
    assert root.presentation.cursor("entries").position.page == 0


def test_jump_stays_in_navigation_row_and_has_current_form_binding() -> None:
    root = MessageRoot(JumpPages(), access=Everyone(), timeout=None, nav=page_jump_nav)
    view = commit_render(root)
    rows = [item for item in view.walk_children() if isinstance(item, discord.ui.ActionRow)]
    assert len(rows) == 1
    assert len(rows[0].children) == 3
    assert root.plan is not None
    field = root.plan.form_bindings["__cursor_seek.entries.jump"].spec.items[0]
    assert isinstance(field, IntField)
    assert (field.minimum, field.maximum, field.default) == (1, 100, 1)


async def test_two_pagers_keep_separate_forms_and_positions() -> None:
    class TwoPagers(sl.Component[sl.ComponentsV2Target]):
        @override
        def render(self):
            return sl.stack(
                Lines(tuple(f"Left {index}" for index in range(100)), overflow=Paginate(key="left", per=1)),
                Lines(tuple(f"Right {index}" for index in range(100)), overflow=Paginate(key="right", per=1)),
            )

    root = MessageRoot(TwoPagers(), access=Everyone(), timeout=None, nav=page_jump_nav)
    commit_render(root)
    left = await open_jump(root, "__cursor_seek.left.jump")
    right = await open_jump(root, "__cursor_seek.right.jump")
    page_input(left)._value = "73"
    await left.on_submit(interaction_harness())
    page_input(right)._value = "42"
    await right.on_submit(interaction_harness())
    assert root.presentation.cursor("left").position.page == 72
    assert root.presentation.cursor("right").position.page == 41


@pytest.mark.parametrize("page,pages,seekable", [(None, 100, True), (0, None, True), (0, 1, True), (0, 100, False)])
def test_jump_is_omitted_without_an_addressable_range(page, pages, seekable: bool) -> None:
    state = replace(materialized_navigation_state("rows", PagePosition(), 100, DEFAULT_CHROME), page=page, extent=pages)

    async def turn(_event) -> None:
        pass

    async def seek(_event, _page: int) -> None:
        pass

    rows = page_jump_nav(NavigationContext(state, turn, turn, seek if seekable else None))
    assert len(rows) == 1
    assert isinstance(rows[0], Row)
    assert len(rows[0].items) == 2


async def test_cached_jump_form_calls_the_current_page_owner() -> None:
    cache = PlanCache()
    seen: list[tuple[str, int]] = []

    async def first(event: sl.PageEvent) -> None:
        seen.append(("first", event.page))

    async def second(event: sl.PageEvent) -> None:
        seen.append(("second", event.page))

    for handler in (first, second):
        result = plan(
            Lines(
                tuple(str(index) for index in range(100)),
                overflow=Paginate(key="entries", per=1, page=sl.controlled(0, handler)),
            ),
            target=DISCORD_V2_DPY27,
            nav=page_jump_nav,
            cache=cache,
        )
    assert result.metrics.cache_hit
    await result.form_bindings["__cursor_seek.entries.jump"].on_submit(engine.submit_event({"page": 73}))
    assert seen == [("second", 72)]
