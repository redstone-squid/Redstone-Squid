"""Routed pagination through layout, scene serialization, and Discord drawing."""

from typing import cast

import discord
import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import scene, testing
from squid_ui.ownership import Controlled, Uncontrolled
from squid_ui.pages import PagePosition
from squid_ui.planning import PlanCache, PlanMemo, Target, plan
from squid_ui.planning.navigation import default_nav, page_jump_form, page_jump_nav, page_select_nav
from squid_ui.presentation import CursorUpdate, PresentationState
from squid_ui.primitives import Lines, Paginate
from squid_ui.primitives.constraints import RoutedPage
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, render_message
from squid_ui_discord.modal import build_form_modal
from squid_ui_discord.routing import Router
from squid_ui_discord.testing import interaction_harness
from squid_ui_widgets import testing as wt


@pytest.mark.parametrize("target", [DISCORD_V1_DPY27, DISCORD_V2_DPY27])
async def test_routed_jump_button_opens_form_after_router_recreation(target: Target) -> None:
    jump = sl.routing.Route("page:jump:{page:int}:{pages:int}")
    result = plan(
        Lines(
            tuple(f"Entry {index}" for index in range(100)),
            overflow=Paginate(
                key="rows",
                per=1,
                page=RoutedPage(
                    2, lambda page: f"page:{page}", jump_route=lambda page, pages: jump.id(page=page, pages=pages)
                ),
            ),
        ),
        target=target,
        nav=page_jump_nav,
    )
    restored = scene.Codec.loads(scene.Codec.dumps(result.scene))
    button = testing.find_all(restored, scene.RoutedButton)[-1]
    assert button.route_id == jump.id(page=2, pages=100)
    assert not result.bindings and not result.form_bindings

    # A fresh host registers its route without retaining anything from planning.
    router = Router()
    seen: list[int] = []

    @router.route(jump)
    async def open_form(interaction: discord.Interaction, page: int, pages: int) -> None:
        spec = page_jump_form(page, pages)

        async def submit(current: discord.Interaction, values: dict[str, object]) -> None:
            parsed = await spec.evaluate(values)
            assert not parsed.errors
            seen.append(cast(int, parsed.values["page"]) - 1)
            await current.response.defer()

        await interaction.response.send_modal(build_form_modal(spec, on_submit=submit))

    interaction = interaction_harness()
    await router.dispatch(interaction, button.route_id)
    modal = interaction.response.send_modal.await_args.args[0]
    field = modal.children[0].component
    assert field.default == "3"
    field._value = "73"
    await modal.on_submit(interaction_harness())
    assert seen == [72]


def test_dropdown_route_is_not_reused_for_a_form_button() -> None:
    result = plan(
        Lines(
            ("a", "b"),
            overflow=Paginate(
                key="rows",
                per=1,
                page=RoutedPage(0, lambda page: f"page:{page}", lambda: "page:select"),
            ),
        ),
        target=DISCORD_V2_DPY27,
        nav=page_jump_nav,
    )
    assert [button.route_id for button in testing.find_all(result.scene, scene.RoutedButton)] == ["page:0", "page:1"]


async def test_routed_widget_encodes_resolved_form_range_and_uses_existing_transition() -> None:
    # Text pressure creates more pages than the requested four entries per page.
    machine = sp.RankedList(tuple("x" * 1500 for _ in range(8)), key="ranked", label=str, value=len, page_size=4)
    encoded: dict[str, tuple[sp.TransitionRoute[sp.RankedListState], int, int]] = {}

    def jump(route: sp.TransitionRoute[sp.RankedListState], page: int, pages: int) -> str:
        key = f"ranking:jump:{route.state.page}:{page}:{pages}"
        encoded[key] = route, page, pages
        return key

    driver = sp.RouteDriver[sp.RankedListState, sl.DiscordTarget](
        lambda route: f"ranking:{route.phase}:{route.state.page}",
        jump_route=jump,
    )
    result = plan(driver.render(machine, machine.initial_state), target=DISCORD_V2_DPY27, nav=page_jump_nav)
    button = testing.find_all(result.scene, scene.RoutedButton)[-1]
    route, page, pages = encoded[button.route_id]
    assert pages == result.scene.pagers[0].pages and pages > 2
    form = page_jump_form(page, pages)
    submitted = await form.evaluate({"page": str(pages)})
    assert not submitted.errors
    restored = sp.RouteDriver[sp.RankedListState, sl.DiscordTarget](lambda _route: "unused")
    state = restored.transition(
        machine, route.state, route.action, values=(str(cast(int, submitted.values["page"]) - 1),)
    )
    assert state.page == pages - 1


@pytest.mark.parametrize("target", [DISCORD_V1_DPY27, DISCORD_V2_DPY27])
@pytest.mark.parametrize("page, expected", [(0, 0), (99, 2), (-1, 2)])
def test_routed_pages_ignore_session_positions_and_round_trip(target: Target, page: int, expected: int) -> None:
    session = PresentationState()
    session.move_cursor("rows", PagePosition(page=1))
    result = plan(
        sl.bullets(
            *(f"entry {index}" for index in range(5)),
            key="rows",
            ordered=True,
            page_size=2,
            page=RoutedPage(page, lambda value: f"page:{value}", lambda: "page:seek"),
        ),
        target=target,
        session=session,
        positions={"rows": PagePosition(page=1)},
        nav=page_select_nav,
    )
    assert result.scene.pagers[0].page == expected
    assert not result.bindings
    assert not any(isinstance(update, CursorUpdate) for update in result.session_updates)
    buttons = testing.find_all(result.scene, scene.RoutedButton)
    assert buttons[0].route_id == f"page:{max(0, expected - 1)}"
    assert buttons[0].disabled == (expected == 0)
    assert buttons[1].disabled == (expected == 2)
    seek = testing.find(result.scene, scene.RoutedSelect)
    assert seek.route_id == "page:seek"
    assert [option.value for option in seek.options if option.default] == [str(expected)]
    assert scene.Codec.loads(scene.Codec.dumps(result.scene)) == result.scene


def test_route_factories_are_re_evaluated_with_a_shared_cache() -> None:
    cache = PlanCache()
    memo = PlanMemo()

    def document(prefix: str) -> Lines:
        return Lines(
            ("one", "two", "three"),
            overflow=Paginate(key="rows", per=1, page=RoutedPage(0, lambda value: f"{prefix}:{value}")),
        )

    for prefix in ("old", "new", "new"):
        result = plan(document(prefix), target=DISCORD_V2_DPY27, nav=default_nav, cache=cache, memo=memo)
        assert all(button.route_id.startswith(prefix) for button in testing.find_all(result.scene, scene.RoutedButton))
        assert not result.metrics.cache_hit


async def test_routed_and_mounted_rankings_share_budget_splits_and_jump_dispatch() -> None:
    def ranking() -> sp.RankedList:
        return sp.RankedList(tuple("x" * 1500 for _ in range(8)), key="ranked", label=str, value=len, page_size=4)

    mounted = wt.mounted(ranking())
    local = plan(mounted.nodes, target=DISCORD_V2_DPY27, nav=page_select_nav)
    routed = wt.routed(ranking(), target=DISCORD_V2_DPY27, nav=page_select_nav)
    assert routed.planned is not None
    assert routed.planned.scene.pagers[0].pages > 2
    assert routed.planned.scene.pagers == local.scene.pagers
    menu = testing.find(routed.planned.scene, scene.RoutedSelect)
    route = next(route for route in routed.routes if route.phase == "input")
    target = menu.options[-1].value
    await local.bindings["__cursor_seek.ranked"].handler(testing.selection_event(target))

    # A new driver and machine can interpret the input route without mounted callbacks.
    restored = sp.RouteDriver[sp.RankedListState, sl.DiscordTarget](lambda _request: "unused")
    state = restored.transition(ranking(), route.state, route.action, values=(target,))
    assert state == mounted.state
    next_page = wt.routed(ranking(), state, target=DISCORD_V2_DPY27)
    assert next_page.planned is not None
    assert next_page.planned.scene.pagers[0].page == int(target)


def test_discord_draws_persistent_buttons_and_jump_options() -> None:
    rendered = render_message(
        Lines(
            ("one", "two", "three"),
            overflow=Paginate(
                key="rows", per=1, page=RoutedPage(1, lambda value: f"page:{value}", lambda: "page:seek")
            ),
        ),
        nav=page_select_nav,
    )
    buttons = [item for item in rendered.view.walk_children() if isinstance(item, discord.ui.Button)]
    select = next(item for item in rendered.view.walk_children() if isinstance(item, discord.ui.Select))
    assert [button.custom_id for button in buttons] == ["page:0", "page:2"]
    assert select.custom_id == "page:seek"
    assert [option.value for option in select.options if option.default] == ["1"]
    assert not rendered.plan.bindings


@pytest.mark.parametrize("target", [DISCORD_V1_DPY27, DISCORD_V2_DPY27])
async def test_semantic_region_uses_its_controlled_owner(target: Target) -> None:
    seen: list[int] = []

    async def change(event: sl.PageEvent) -> None:
        seen.append(event.page)

    document = sl.stack(*(sl.aside(sl.paragraph(str(index) * 20)) for index in range(4))).paged(
        key="region", chars=40, page=Controlled(99, change)
    )
    result = plan(document, target=target, nav=default_nav)
    assert result.scene.pagers[0].page == result.scene.pagers[0].pages - 1
    assert not any(isinstance(update, CursorUpdate) for update in result.session_updates)
    await result.bindings["__cursor_previous.region"].handler(testing.press_event())
    assert seen == [result.scene.pagers[0].page - 1]


async def test_semantic_list_preserves_global_numbering_and_controlled_turns() -> None:
    seen: list[int] = []

    async def change(event: sl.PageEvent) -> None:
        seen.append(event.page)

    result = plan(
        sl.bullets("a", "b", "c", key="list", ordered=True, page_size=2, page=Controlled(1, change)),
        target=DISCORD_V2_DPY27,
        nav=default_nav,
    )
    assert "3. c" in testing.texts(result.scene)
    await result.bindings["__cursor_previous.list"].handler(testing.press_event())
    assert seen == [0]


def test_uncontrolled_semantic_region_accepts_last_page_seed() -> None:
    document = sl.paged(
        sl.stack(*(sl.paragraph(str(index) * 20) for index in range(4))), key="region", chars=40, page=Uncontrolled(-1)
    )
    result = plan(document, target=DISCORD_V2_DPY27, nav=default_nav)
    assert result.scene.pagers[0].page == result.scene.pagers[0].pages - 1
    assert any(isinstance(update, CursorUpdate) for update in result.session_updates)


def test_routed_multi_choice_draws_selected_defaults_and_clears_only_its_window() -> None:
    machine = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", tuple(sl.choice(str(i), key=str(i)) for i in range(30))),),
        committed=("0", "25"),
        maximum=4,
    )
    state = machine.transition(machine.initial_state, "page:roles:next")
    rendered = wt.routed(machine, state, target=DISCORD_V2_DPY27)
    assert rendered.planned is not None
    picker = testing.find(rendered.planned.scene, scene.RoutedSelect)
    assert [option.value for option in picker.options if option.default] == ["25"]
    route = next(route for route in rendered.routes if route.phase == "input")
    cleared = machine.transition(route.state, route.action, values=())
    assert cleared.staged == ("0",)
    assert cleared.committed == ("0", "25")


def test_routed_region_uses_route_navigation_without_live_bindings() -> None:
    result = plan(
        sl.paged(
            sl.stack(*(sl.paragraph(str(index) * 20) for index in range(4))),
            key="region",
            chars=40,
            page=RoutedPage(-1, lambda value: f"region:{value}"),
        ),
        target=DISCORD_V2_DPY27,
        nav=page_select_nav,
    )
    assert not result.bindings
    assert testing.find_all(result.scene, scene.RoutedButton)
    assert not testing.find_all(result.scene, scene.RoutedSelect), "jump is optional for a routed page"


def test_two_pagers_in_one_classic_embed_keep_their_navigation() -> None:
    document = sl.stack(
        sl.bullets("a", "b", "c", key="left", page_size=1, page=RoutedPage(1, lambda page: f"left:{page}")),
        sl.bullets("d", "e", "f", key="right", page_size=1, page=RoutedPage(2, lambda page: f"right:{page}")),
    )
    result = plan(document, target=DISCORD_V1_DPY27, nav=default_nav)
    assert [button.route_id for button in testing.find_all(result.scene, scene.RoutedButton)] == [
        "left:0",
        "left:2",
        "right:1",
        "right:2",
    ]
    assert [(pager.key, pager.page) for pager in result.scene.pagers] == [("left", 1), ("right", 2)]
