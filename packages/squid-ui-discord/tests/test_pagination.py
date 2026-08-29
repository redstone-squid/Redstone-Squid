"""Engine pagination and ModalSpec tests."""

import discord
import pytest
from discord.state import ConnectionState
from discord.ui.view import ViewStore
from hypothesis import given
from hypothesis import strategies as st

import squid_ui as sl
from squid_ui import Component, field, fields, paragraph, section, truncate
from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import SolveNoteCode, measure
from squid_ui.planning.layout_measurement.costing import component_count
from squid_ui.planning.layout_measurement.model import MeasuredText
from squid_ui.planning.layout_measurement.text import split_pages
from squid_ui.planning.limits import Axis
from squid_ui.planning.navigation import NavigationContext, default_nav, page_select_nav
from squid_ui.planning.semantic_adaptation.lowering import lower_semantics
from squid_ui.primitives import (
    Button,
    Code,
    Heading,
    Lines,
    Paginate,
    Row,
    Text,
)
from squid_ui.runtime import PresentationState
from squid_ui.semantic import Item, Items, Paragraph
from squid_ui.sources import Direction, Position, PositionResolver
from squid_ui.text import NEUTRAL
from squid_ui_discord import V2_LIMITS as LIMITS
from squid_ui_discord import Everyone, MessageRoot, conform
from squid_ui_discord.modal import LabelSpec, ModalSpec, TextInputSpec, build_modal
from squid_ui_discord.testing import assert_within_limits, commit_render, interaction_harness


class TestSplitPages:
    def test_short_text_is_one_page(self):
        assert split_pages("a\nb", 100) == ["a\nb"]

    def test_splits_on_line_boundaries(self):
        pages = split_pages("aaa\nbbb\nccc", 7)
        assert pages == ["aaa\nbbb", "ccc"]

    def test_hard_splits_single_long_line_without_inventing_boundaries(self):
        pages = split_pages("x" * 25, 10)
        assert pages == ["x" * 10, "x" * 10, "x" * 5]
        assert "".join(pages) == "x" * 25

    def test_no_content_is_lost(self):
        text = "\n".join(f"line {index} " + "y" * (index % 30) for index in range(200))
        pages = split_pages(text, 300)
        assert "\n".join(pages) == text
        assert all(len(page) <= 300 for page in pages)

    def test_large_inputs_use_the_same_exact_balancing_objective(self):
        text = "\n".join("x" for _ in range(300))

        pages = split_pages(text, 39)

        assert len(pages) == 15
        assert {len(page) for page in pages} == {39}
        assert "\n".join(pages) == text


class TestPositionPolicy:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            (
                {
                    "override": Position("override", 8, Direction.FORWARD),
                    "anchored": Position("anchor", 7),
                    "stale": True,
                    "stored": Position("stored", 6),
                    "initial": Position(offset=5),
                },
                Position("override", 8, Direction.FORWARD),
            ),
            (
                {
                    "anchored": Position("anchor", 7),
                    "stale": True,
                    "stored": Position("stored", 6),
                    "initial": Position(offset=5),
                },
                Position("anchor", 7),
            ),
            (
                {"stale": True, "stored": Position("stored", 6), "fallback": Position("fallback", 4)},
                Position("fallback", 4),
            ),
            ({"stored": Position("stored", 6), "initial": Position(offset=5)}, Position("stored", 6)),
            ({"initial": Position("initial", 5)}, Position("initial", 5)),
        ],
    )
    def test_precedence(self, kwargs, expected) -> None:
        assert PositionResolver().resolve(**kwargs) == expected

    def test_clamps_only_the_offset(self) -> None:
        policy = PositionResolver()
        assert policy.resolve(override=Position("item", -3, Direction.BACKWARD), upper_bound=8) == Position(
            "item", 0, Direction.BACKWARD
        )
        assert policy.resolve(override=Position("item", 20, Direction.FORWARD), upper_bound=8) == Position(
            "item", 8, Direction.FORWARD
        )


class TestSolvePagination:
    def test_overflowing_paginate_node_produces_pages(self):
        body = "\n".join(f"line {index:04d}" for index in range(1000))
        solved = measure([Heading("Report"), Code(body, overflow=Paginate())])
        assert solved.pages > 1
        assert solved.pager is not None

    def test_footer_is_budgeted_not_guessed(self):
        # Every page, including its footer, fits the real budget: the PAGE_CHARS killer.
        body = "\n".join("z" * 80 for _ in range(200))
        solved = measure([Heading("H" * 200), Code(body, overflow=Paginate())])
        assert solved.pager is not None
        for page in range(solved.pager.pages):
            solved.pager.select(page)
            total = _total_text(solved)
            assert total <= LIMITS.total_text, f"page {page} is {total}"

    def test_fitting_paginate_node_needs_no_pager(self):
        solved = measure([Text("short", overflow=Paginate())])
        assert solved.pager is None
        assert solved.pages == 1

    def test_multiple_paginate_nodes_are_independent(self):
        solved = measure(
            [
                Text("a" * 3000, overflow=Paginate(key="alpha")),
                Text("b" * 3000, overflow=Paginate(key="beta")),
            ],
            position={"alpha": Position(offset=0), "beta": Position(offset=1)},
        )
        assert [(pager.key, pager.page) for pager in solved.pagers] == [("alpha", 0), ("beta", 1)]

    def test_a_position_token_is_an_explicit_page_override(self):
        body = "\n".join(f"line {index:04d}" for index in range(1000))
        solved = measure([Code(body, overflow=Paginate(key="entries"))], position={"entries": Position(offset=2)})

        assert solved.pager is not None and solved.pager.page == 2


def _total_text(solved) -> int:
    def walk(children) -> int:
        total = 0
        for child in children:
            if isinstance(child, MeasuredText):
                total += len(child.content)
            elif hasattr(child, "texts"):
                total += sum(len(text.content) for text in child.texts)
            elif hasattr(child, "children"):
                total += walk(child.children)
        return total

    return walk(solved.children)


class Browser(Component[sl.ComponentsV2Target]):
    def render(self):
        body = "\n".join(f"entry {index:04d}" for index in range(2000))
        return [Heading("Entries"), Code(body, overflow=Paginate(key="entries"))]


class TwoBrowsers(Component[sl.ComponentsV2Target]):
    def __init__(self) -> None:
        self.left_version = "old"

    def render(self):
        left = tuple(f"{self.left_version} left {index}" for index in range(30))
        right = tuple(f"right {index}" for index in range(30))
        return [
            Lines(left, overflow=Paginate(key="left", per=10)),
            Lines(right, overflow=Paginate(key="right", per=10)),
        ]


class Catalog(Component[sl.ComponentsV2Target]):
    """A semantic picker whose list can shift under the reader."""

    def __init__(self) -> None:
        self.lead: tuple[str, ...] = ()

    def render(self):
        keys = (*self.lead, *(str(index) for index in range(36)))
        return [
            Items(
                "catalog",
                tuple(Item(key, sl.semantic.ItemLabel(f"Item {key}"), (Paragraph("detail"),)) for key in keys),
            )
        ]


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
        """The fingerprint dance used to make every flush plan twice."""
        from squid_ui_discord import message_root as message_root_module

        calls = 0
        planner = message_root_module.plan_document

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return planner(*args, **kwargs)

        monkeypatch.setattr(message_root_module, "plan_document", counted)
        message_root = MessageRoot(TwoBrowsers(), access=Everyone(), timeout=None)
        commit_render(message_root)
        assert calls == 1

    async def test_a_paged_picker_follows_its_anchor_through_the_root(self):
        """The production path, which used to launder every cursor through `page=`."""
        catalog = Catalog()
        message_root = MessageRoot(catalog, access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("__cursor_next.catalog.items", interaction_harness())
        assert message_root.presentation.cursor("catalog.items").position.offset == 1

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
            label = f"{context.position.offset + 1}/{context.state.extent}"
            return (Row((Button(label=label, on_click=context.on_next, key="jump"),)),)

        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=nav)
        view = commit_render(message_root)
        assert [button.label for button in self._nav_buttons(view)] == [
            f"1/{message_root.presentation.cursor('entries').extent}"
        ]

        await message_root.dispatch("jump", interaction_harness())

        assert message_root.presentation.cursor("entries").position.offset == 1

    async def test_a_materialized_cursor_seeks_to_a_page(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        view = commit_render(message_root)
        jump = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select))
        assert jump.custom_id is not None and jump.custom_id.endswith("__cursor_seek.entries")

        await message_root.dispatch("__cursor_seek.entries", interaction_harness(), ["3"])

        assert message_root.presentation.cursor("entries").position.offset == 3

    async def test_seeking_past_the_end_clamps_to_the_last_page(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        commit_render(message_root)
        extent = message_root.presentation.cursor("entries").extent

        await message_root.dispatch("__cursor_seek.entries", interaction_harness(), ["9999"])

        assert message_root.presentation.cursor("entries").position.offset == extent - 1

    async def test_seeking_to_the_visible_page_is_a_clean_noop(self):
        message_root = MessageRoot(Browser(), access=Everyone(), timeout=None, nav=page_select_nav)
        commit_render(message_root)
        interaction = interaction_harness()

        await message_root.dispatch("__cursor_seek.entries", interaction, ["0"])

        interaction.response.defer.assert_awaited_once()

    def test_the_stock_factory_still_draws_no_jump_control(self):
        """`page_select_nav` is opt-in: a select costs a whole component row."""
        view = commit_render(MessageRoot(Browser(), access=Everyone(), timeout=None))

        assert not [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]

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

        assert {key: cursor.position.offset for key, cursor in message_root.presentation.cursors.items()} == {
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

        assert {key: cursor.position.offset for key, cursor in message_root.presentation.cursors.items()} == {
            "left": 0,
            "right": 1,
        }


class TestCountPages:
    def _entries(self, count: int) -> tuple[str, ...]:
        return tuple(f"entry {index}" for index in range(count))

    def test_a_short_list_paginates_by_count_not_by_pressure(self):
        solved = measure([Lines(self._entries(25), overflow=Paginate(per=10))])
        assert solved.pages == 3

    def test_no_entry_is_lost_across_the_pages(self):
        entries = self._entries(25)
        solved = measure([Lines(entries, overflow=Paginate(per=10))])
        assert solved.pager is not None
        assert "\n".join(solved.pager.fragments).split("\n") == list(entries)

    def test_a_list_that_fits_one_page_has_no_pager(self):
        solved = measure([Lines(self._entries(4), overflow=Paginate(per=10))])
        assert solved.pager is None

    def test_an_oversized_count_page_is_split_further_to_fit(self):
        solved = measure([Lines(tuple("x" * 3000 for _ in range(4)), overflow=Paginate(per=2))])
        assert solved.pager is not None
        assert solved.pager.pages > 2
        for page in range(solved.pager.pages):
            solved.pager.select(page)
            assert _total_text(solved) <= LIMITS.total_text

    def test_the_node_footer_overrides_chrome(self):
        solved = measure([Lines(self._entries(25), overflow=Paginate(per=10, footer=lambda p, n: f"{p}/{n} · 25"))])
        assert solved.pager is not None
        assert any("1/3 · 25" in child.content for child in solved.children if isinstance(child, MeasuredText))

    def test_count_pages_on_a_non_lines_node_fall_back_to_budget_pages(self):
        solved = measure([Text("short", overflow=Paginate(per=10))])
        assert solved.pager is None
        assert any(note.code is SolveNoteCode.PAGINATE_PER_FALLBACK for note in solved.notes)

    def test_per_must_be_positive(self):
        with pytest.raises(ValueError, match="at least 1"):
            Paginate(per=0)


class TestSolverNav:
    def _paginated(self) -> list:
        return [Code("\n".join(f"line {index:04d}" for index in range(1000)), overflow=Paginate())]

    def _nav(self, state):
        async def move(interaction) -> None: ...

        return default_nav(NavigationContext(state, move, move))

    def test_nav_nodes_are_realized_into_the_document(self):
        solved = measure(self._paginated(), nav=self._nav)
        assert isinstance(solved.children[-1], Row)

    def test_an_unpaginated_document_gets_no_nav(self):
        solved = measure([Text("short")], nav=self._nav)
        assert not any(isinstance(child, Row) for child in solved.children)

    def test_the_page_is_clamped_and_reported(self):
        assert measure(self._paginated(), position=Position(offset=999)).page == measure(self._paginated()).pages - 1
        assert measure(self._paginated(), position=Position(offset=-5)).page == 0

    def test_a_text_bearing_nav_factory_is_rejected(self):
        # `NavNode` rejects this statically; the runtime guard is for callers without a
        # type checker, so the suppression here is the test doing its job.
        with pytest.raises(ValueError, match="component-bearing"):
            measure(self._paginated(), nav=lambda state: [Text(f"position {state.position.offset}")])  # type: ignore


class TestRepage:
    """Turning a page is a projection: the fit that produced it does not change."""

    def _paginated(self) -> list:
        return [Code("\n".join(f"line {index:04d}" for index in range(1000)), overflow=Paginate(key="lines"))]

    def _nav(self, state):
        async def move(interaction) -> None: ...

        return default_nav(NavigationContext(state, move, move))

    def test_repage_moves_the_page_without_moving_the_fit(self):
        solved = measure(self._paginated(), nav=self._nav)
        before = solved.cost.get(Axis.COMPONENTS)
        pager = solved.pager
        assert pager is not None and solved.pages > 2
        solved.reposition({"lines": Position(offset=2)})
        direct = measure(self._paginated(), nav=self._nav, position=Position(offset=2)).pager
        assert direct is not None
        assert pager.page == 2
        assert pager.slot.content == direct.slot.content
        assert component_count(solved.children) == before

    def test_repage_redraws_the_nav_it_replaced(self):
        solved = measure(self._paginated(), nav=self._nav)
        previous = self._previous_button(solved)
        assert previous.disabled  # on page 0
        solved.reposition({"lines": Position(offset=1)})
        assert not self._previous_button(solved).disabled

    @staticmethod
    def _previous_button(solved) -> Button:
        row = solved.children[-1]
        assert isinstance(row, Row)
        button = row.items[0]
        assert isinstance(button, Button)
        return button

    def test_repage_clamps_like_the_solver_does(self):
        solved = measure(self._paginated(), nav=self._nav)
        solved.reposition({"lines": Position(offset=999)})
        assert solved.page == solved.pages - 1
        solved.reposition({"lines": Position(offset=-5)})
        assert solved.page == 0

    def test_a_nav_factory_that_hides_controls_between_pages_is_rejected(self):
        def hiding(state):
            controls = self._nav(state)
            return controls if state.position.offset else []

        solved = measure(self._paginated(), nav=hiding)
        with pytest.raises(LayoutInvariantError, match="changed shape between pages"):
            solved.reposition({"lines": Position(offset=1)})


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
        next(iter(modal._inputs.values()))._value = "steve"  # pyrefly: ignore

        await modal.on_submit(interaction_harness())

        assert received == {"name": "steve"}


@given(st.text(min_size=4500, max_size=9000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_paginated_documents_fit_on_every_page(body):
    card_node = section(sl.heading("Title"), truncate(paragraph("intro")), fields(field("k", "v")))
    lowered = lower_semantics(
        [card_node],
        limits=LIMITS,
        chrome=DEFAULT_CHROME,
        localization=NEUTRAL,
        session=PresentationState(),
    ).nodes
    solved = measure([*lowered, Code(body, overflow=Paginate())])
    if solved.pager is None:
        return
    for page in range(solved.pager.pages):
        solved.pager.select(page)
        assert _total_text(solved) <= LIMITS.total_text


@given(st.integers(min_value=0, max_value=60), st.integers(min_value=1, max_value=12))
def test_count_pages_hold_every_entry_exactly_once(count, per):
    entries = tuple(f"entry {index}" for index in range(count))
    solved = measure([Lines(entries, overflow=Paginate(per=per))])
    if solved.pager is None:
        assert count <= per
        return
    assert solved.pager.pages == -(-count // per)
    assert [line for fragment in solved.pager.fragments for line in fragment.split("\n")] == list(entries)


def test_the_solver_counts_the_nav_it_realized():
    # The assertion that replaced NAV_ROW_COMPONENTS: nav is counted because it is realized,
    # so the solver's component budget matches what the built view actually contains.
    async def move(interaction) -> None: ...

    def nav(state):
        return default_nav(NavigationContext(state, move, move))

    view = commit_render(MessageRoot(Browser(), access=Everyone(), timeout=None))
    solved = measure(Browser().render(), nav=nav)
    assert component_count(solved.children) == len(list(view.walk_children()))
