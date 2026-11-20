"""Engine pagination and ModalSpec tests."""

from dataclasses import replace
from typing import override

import discord
import pytest
from discord.state import ConnectionState
from discord.ui.view import ViewStore
from hypothesis import given, settings
from hypothesis import strategies as st

import squid_ui as sl
from squid_ui import Component, field, fields, paragraph, section, truncate
from squid_ui.authoring.semantic import Item, Items, Paragraph
from squid_ui.ext.discord import DISCORD_V2_DPY27, Everyone, MessageRoot, conform
from squid_ui.ext.discord import V2_LIMITS as LIMITS
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.modal import LabelSpec, ModalSpec, TextInputSpec, build_modal
from squid_ui.ext.discord.navigation_controls import default_nav, page_select_nav
from squid_ui.ext.discord.planning import components_v2_target
from squid_ui.ext.discord.primitives import LAST_PAGE, Button, Code, Heading, Lines, Paginate, Row
from squid_ui.ext.discord.target_types import ComponentsV2Target, DiscordAdapter
from squid_ui.ext.discord.testing import assert_within_limits, commit_render, interaction_harness, query
from squid_ui.pages import PagePosition, resolve_page_position
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.resources import Axis
from squid_ui.runtime import PresentationState
from squid_ui.selectors import select as select_node


class TestPositionPolicy:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            (
                {
                    "override": PagePosition("override", 8),
                    "anchored": PagePosition("anchor", 7),
                    "stale": True,
                    "stored": PagePosition("stored", 6),
                    "initial": PagePosition(page=5),
                },
                PagePosition("override", 8),
            ),
            (
                {
                    "anchored": PagePosition("anchor", 7),
                    "stale": True,
                    "stored": PagePosition("stored", 6),
                    "initial": PagePosition(page=5),
                },
                PagePosition("anchor", 7),
            ),
            (
                {"stale": True, "stored": PagePosition("stored", 6), "fallback": PagePosition("fallback", 4)},
                PagePosition("fallback", 4),
            ),
            ({"stored": PagePosition("stored", 6), "initial": PagePosition(page=5)}, PagePosition("stored", 6)),
            ({"initial": PagePosition("initial", 5)}, PagePosition("initial", 5)),
        ],
    )
    def test_precedence(self, kwargs, expected) -> None:
        assert resolve_page_position(**kwargs) == expected

    def test_clamps_only_the_offset(self) -> None:
        policy = resolve_page_position
        assert policy(override=PagePosition("item", -3), upper_bound=8) == PagePosition("item", 0)
        assert policy(override=PagePosition("item", 20), upper_bound=8) == PagePosition("item", 8)


class Browser(Component[ComponentsV2Target]):
    @override
    def render(self):
        # Navigation needs several budget-driven pages, not thousands of split points.
        body = "\n".join(f"entry {index:04d} " + "x" * 990 for index in range(20))
        return [Heading("Entries"), Code(body, overflow=Paginate(key="entries"))]


class TwoBrowsers(Component[ComponentsV2Target]):
    def __init__(self) -> None:
        self.left_version = "old"

    @override
    def render(self):
        left = tuple(f"{self.left_version} left {index}" for index in range(30))
        right = tuple(f"right {index}" for index in range(30))
        return [
            Lines(left, overflow=Paginate(key="left", per=10)),
            Lines(right, overflow=Paginate(key="right", per=10)),
        ]


class Catalog(Component[ComponentsV2Target]):
    """A semantic picker whose list can shift under the reader."""

    def __init__(self) -> None:
        self.lead: tuple[str, ...] = ()

    @override
    def render(self):
        keys = (*self.lead, *(str(index) for index in range(36)))
        return [
            Items(
                "catalog",
                tuple(Item(key, sl.semantic.ItemLabel(f"Item {key}"), (Paragraph("detail"),)) for key in keys),
            )
        ]


class ControlledLog(Component[ComponentsV2Target]):
    """Keeps its own page, as a filtered list does so a new filter can return to page one."""

    page: int = sl.state(0)

    def __init__(self, page: int = 0, *, accept: bool = True) -> None:
        self.page = page
        self.accept = accept
        self.turns: list[int] = []

    async def _turn(self, event: sl.PageEvent) -> None:
        self.turns.append(event.page)
        if self.accept:
            self.page = event.page

    @override
    def render(self):
        entries = tuple(f"entry {index}" for index in range(30))
        return [Lines(entries, overflow=Paginate(key="log", per=10, page=sl.controlled(self.page, self._turn)))]


class TestControlledPagination:
    def _footers(self, view) -> list[str]:
        return [child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay)]

    def test_the_application_page_is_shown_and_no_cursor_is_stored(self):
        message_root = MessageRoot(ControlledLog(page=2), access=Everyone(), timeout=None)
        view = commit_render(message_root)
        assert any("Page 3 of 3" in text for text in self._footers(view))
        assert "log" not in message_root.presentation.cursors

    def test_an_out_of_range_page_is_clamped(self):
        view = commit_render(MessageRoot(ControlledLog(page=99), access=Everyone(), timeout=None))
        assert any("Page 3 of 3" in text for text in self._footers(view))

    async def test_a_turn_reaches_the_application(self):
        log = ControlledLog(page=2)
        message_root = MessageRoot(log, access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = interaction_harness()

        await message_root.dispatch("__cursor_previous.log", interaction)

        assert log.turns == [1]
        edited = interaction.response.edit_message.await_args.kwargs["view"]
        assert any("Page 2 of 3" in text for text in self._footers(edited))

    async def test_a_turn_the_application_ignores_leaves_the_page(self):
        log = ControlledLog(page=2, accept=False)
        message_root = MessageRoot(log, access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("__cursor_previous.log", interaction_harness())

        assert log.turns == [1]
        assert any("Page 3 of 3" in text for text in self._footers(commit_render(message_root)))

    def test_last_page_opens_an_uncontrolled_pager_at_the_end(self):
        entries = tuple(f"entry {index}" for index in range(30))
        result = plan(Lines(entries, overflow=Paginate(key="log", per=10, page=LAST_PAGE)), target=DISCORD_V2_DPY27)
        assert result.scene.pagers[0].page == 2

    def test_a_controlled_pager_needs_a_key(self):
        async def ignore(event: sl.PageEvent) -> None: ...

        with pytest.raises(ValueError, match="needs a key"):
            Paginate(page=sl.controlled(0, ignore))


class TestMountPagination:
    def _nav_buttons(self, view) -> list[discord.ui.Button]:
        return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    def test_nav_row_is_synthesized(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None)
        view = commit_render(message_root)
        prev_button, next_button = self._nav_buttons(view)
        assert prev_button.disabled  # first page
        assert not next_button.disabled
        assert_within_limits(view)
        assert conform(view) == []

    async def test_next_advances_and_edges_disable(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = interaction_harness()

        await message_root.dispatch("__cursor_next.entries", interaction)

        edited = interaction.response.edit_message.await_args.kwargs["view"]
        prev_button, _ = self._nav_buttons(edited)
        assert not prev_button.disabled
        footers = [c.content for c in edited.walk_children() if isinstance(c, discord.ui.TextDisplay)]
        assert any("Page 2 of" in text for text in footers)

    def test_the_message_root_draws_once_per_render(self, monkeypatch):
        """One committed render must make one planner call, including cursor fingerprinting."""
        from squid_ui.ext.discord.messages import root as message_root_module

        calls = 0
        planner = message_root_module.plan_document  # pyright: ignore[reportPrivateImportUsage]  # monkeypatch target: the caller resolves it here

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return planner(*args, **kwargs)

        monkeypatch.setattr(message_root_module, "plan_document", counted)
        message_root = MessageRoot(TwoBrowsers(), access=Everyone(), timeout=None)
        commit_render(message_root)
        assert calls == 1

    async def test_a_paged_picker_follows_its_anchor_through_the_root(self):
        """The mounted path must preserve anchor identity when preceding entries are inserted."""
        catalog = Catalog()
        message_root = MessageRoot(catalog, access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("__cursor_next.catalog.items", interaction_harness())
        assert message_root.presentation.cursor("catalog.items").position.page == 1

        catalog.lead = ("new",)
        view = commit_render(message_root)

        # One item joined the head, so the reader's page slid by one to keep them on it.
        values = [
            option.value
            for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
            for option in item.options
        ]
        assert "24" in values

    def test_stopping_replaced_view_keeps_new_paginator_registered(self):
        """discord.py must retain the new generation after MessageRoot stops the old one."""
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None)
        first = commit_render(message_root)
        second = commit_render(message_root)
        message_id = 42
        store = ViewStore(object.__new__(ConnectionState))

        store.add_view(first, message_id)
        store.add_view(second, message_id)
        first.stop()

        next_button = next(button for button in self._nav_buttons(second) if button.label == "Next")
        assert (next_button.type.value, next_button.custom_id) in store._views[message_id]

    async def test_a_custom_nav_factory_replaces_the_stock_controls(self):
        def nav(context):
            label = f"{context.index + 1}/{context.state.extent}"
            return (Row((Button(label=label, on_click=context.on_next, key="jump"),)),)

        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=nav)
        view = commit_render(message_root)
        assert [button.label for button in self._nav_buttons(view)] == [
            f"1/{message_root.presentation.cursor('entries').extent}"
        ]

        await message_root.dispatch("jump", interaction_harness())

        assert message_root.presentation.cursor("entries").position.page == 1

    async def test_a_materialized_cursor_seeks_to_a_page(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        view = commit_render(message_root)
        jump = query(view, select_node(discord.ui.Select)).all()[0].node
        assert jump.custom_id is not None and jump.custom_id.endswith("__cursor_seek.entries")

        await message_root.dispatch("__cursor_seek.entries", interaction_harness(), ["3"])

        assert message_root.presentation.cursor("entries").position.page == 3

    async def test_seeking_past_the_end_clamps_to_the_last_page(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        commit_render(message_root)
        extent = message_root.presentation.cursor("entries").extent

        await message_root.dispatch("__cursor_seek.entries", interaction_harness(), ["9999"])

        assert message_root.presentation.cursor("entries").position.page == extent - 1

    async def test_seeking_to_the_visible_page_is_a_clean_noop(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        commit_render(message_root)
        interaction = interaction_harness()

        await message_root.dispatch("__cursor_seek.entries", interaction, ["0"])

        interaction.response.defer.assert_awaited_once()

    def test_the_stock_factory_still_draws_no_jump_control(self):
        """`page_select_nav` is opt-in: a select costs a whole component row."""
        view = commit_render(MessageRoot(Browser(), access=Everyone(), timeout=None))

        assert not query(view, "Select").exists()

    async def test_prev_at_first_page_is_a_clean_noop(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = interaction_harness()

        await message_root.dispatch("__cursor_previous.entries", interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_two_pagers_advance_independently(self):
        message_root = MessageRoot(TwoBrowsers(), access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("__cursor_next.left", interaction_harness())

        assert {key: cursor.position.page for key, cursor in message_root.presentation.cursors.items()} == {
            "left": 1,
            "right": 0,
        }

    async def test_changed_content_resets_only_its_pager(self):
        component = TwoBrowsers()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("__cursor_next.left", interaction_harness())
        await message_root.dispatch("__cursor_next.right", interaction_harness())

        component.left_version = "new"
        message_root.invalidate()
        commit_render(message_root)

        assert {key: cursor.position.page for key, cursor in message_root.presentation.cursors.items()} == {
            "left": 0,
            "right": 1,
        }


class TestBuildModal:
    def test_oversized_spec_is_clamped_by_construction(self):
        # The live-bug shape: a default joined from unbounded user data.
        spec = ModalSpec(
            title="Edit Build " + "x" * 100,
            items=(
                LabelSpec(
                    text="Image URLs " + "y" * 100,
                    input=TextInputSpec(label="urls", default=", ".join(f"https://e.invalid/{i}" for i in range(400))),
                ),
            ),
        )
        modal = build_modal(spec)
        assert_within_limits(modal)

    async def test_submit_handler_receives_values_by_key(self):
        received = {}

        async def on_submit(interaction, values):
            received.update(values)

        spec = ModalSpec(title="T", items=(LabelSpec(text="Name", input=TextInputSpec(label="n", key="name")),))
        modal = build_modal(spec, on_submit=on_submit)
        next(iter(modal._inputs.values()))._value = "steve"  # pyright: ignore[reportAttributeAccessIssue]  # pyrefly: ignore

        await modal.on_submit(interaction_harness())

        assert received == {"name": "steve"}


def test_paginated_code_skips_cuts_that_cannot_fit() -> None:
    target = components_v2_target(
        AdapterProfile(DiscordAdapter, "pagination-tests", "1"), limits=replace(LIMITS, total_text=160)
    )
    body = "\n".join(("a" * 30, "b" * 65, "c" * 30, "d" * 65))
    result = plan(Code(body, overflow=Paginate(boundary="\n")), target=target)

    assert result.allocation is not None
    ((identity, region),) = result.allocation.regions.items()
    assert len(region.cuts) == 2
    assert result.metrics.target_evaluations == result.metrics.certified_combinations
    for index in range(len(region.cuts)):
        assert result.for_page({identity: index}).cost.get(Axis.DISPLAY_TEXT) <= 160


# Public planning certifies every page; this property has no timing contract.
@settings(deadline=None)
@given(st.text(min_size=4500, max_size=9000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_paginated_documents_fit_on_every_page(body):
    card_node = section(sl.heading("Title"), truncate(paragraph("intro")), fields(field("k", "v")))
    result = plan(
        sl.group(card_node, Code(body, overflow=Paginate())),
        target=components_v2_target(AdapterProfile(DiscordAdapter, "pagination-tests", "1")),
    )
    assert result.cost.get(Axis.DISPLAY_TEXT) <= LIMITS.total_text
    assert result.allocation is not None
    for occurrence, region in result.allocation.regions.items():
        for page in range(len(region.cuts)):
            selected = result.for_page({occurrence: page})
            assert selected.cost.get(Axis.DISPLAY_TEXT) <= LIMITS.total_text


@settings(deadline=None)
@given(st.integers(min_value=0, max_value=60), st.integers(min_value=1, max_value=12))
def test_count_pages_hold_every_entry_exactly_once(count, per):
    entries = tuple(f"entry {index}" for index in range(count))
    result = plan(Lines(entries, overflow=Paginate(key="entries", per=per)), target=DISCORD_V2_DPY27)
    if not result.scene.pagers:
        assert count <= per
        return
    assert result.scene.pagers[0].pages == -(-count // per)
    assert result.allocation is not None
    occurrence = next(iter(result.allocation.regions))
    collected = []
    assert result.scene.pagers[0].pages is not None
    for page in range(result.scene.pagers[0].pages):
        selected = result.for_page({occurrence: page})
        collected.extend(
            line
            for node in selected.scene.body.children
            if isinstance(node, discord_scene.Text)
            for line in node.content.splitlines()
            if line.startswith("entry ")
        )
    assert collected == list(entries)


def test_the_solver_counts_the_nav_it_realized():
    # Count the realized navigation controls so planning and drawing charge the same components.
    view = commit_render(MessageRoot(Browser(), access=Everyone(), timeout=None))
    result = plan(Browser().render(), target=DISCORD_V2_DPY27, nav=default_nav)
    assert result.cost.get(Axis.COMPONENTS) == len(list(view.walk_children()))


@pytest.mark.parametrize("oversized", [False, True])
def test_initial_anchor_resolves_after_measurement_and_is_consumed_only_on_delivery(oversized: bool) -> None:
    presentation = PresentationState()
    entries = tuple(sl.bullet("same " * (900 if oversized else 250), key=str(index)) for index in range(5))
    document = sl.bullets(*entries, key="entries", page=sl.pages.InitialPage(anchor="3"))
    first = sl.planning.plan(document, target=DISCORD_V2_DPY27, session=presentation)
    assert first.scene.pagers[0].page > 0
    assert not presentation.cursors
    retry = sl.planning.plan(document, target=DISCORD_V2_DPY27, session=presentation)
    assert retry.scene.pagers[0].page == first.scene.pagers[0].page
    presentation.apply_updates(first.session_updates)
    assert "entries" in presentation.cursors
    presentation.move_cursor("entries", PagePosition(page=0))
    manual = sl.planning.plan(document, target=DISCORD_V2_DPY27, session=presentation)
    assert manual.scene.pagers[0].page == 0


def test_single_page_initial_anchor_is_consumed_without_navigation_chrome() -> None:
    presentation = PresentationState()
    document = sl.bullets(sl.bullet("Only entry", key="one"), key="entries", page=sl.pages.InitialPage(anchor="one"))
    planned = sl.planning.plan(document, target=DISCORD_V2_DPY27, session=presentation)
    assert not planned.scene.pagers
    assert not presentation.cursors
    presentation.apply_updates(planned.session_updates)
    assert "entries" in presentation.cursors
    assert presentation.cursor("entries").extent == 1
