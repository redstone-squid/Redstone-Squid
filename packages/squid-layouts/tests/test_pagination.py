"""Engine pagination and ModalSpec tests."""

from types import SimpleNamespace
from typing import cast

import discord
import pytest
from discord.state import ConnectionState
from discord.ui.view import ViewStore
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    DEFAULT_CHROME,
    Component,
    field,
    fields,
    paragraph,
    section,
    truncate,
)
from squid_layouts.discord import (
    DEFAULT_LIMITS as LIMITS,
)
from squid_layouts.discord import (
    LabelSpec,
    ModalSpec,
    Mount,
    PageContext,
    TextInputSpec,
    build_modal,
    conform,
    default_nav,
)
from squid_layouts.discord.testing import assert_within_limits, commit_render, fake_interaction
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning import solve
from squid_layouts.planning.adaptation import lower_semantics
from squid_layouts.planning.solve import RText, _component_count, split_pages
from squid_layouts.primitives import (
    Button,
    Code,
    Heading,
    Lines,
    Paginate,
    Row,
    Text,
)
from squid_layouts.runtime import PresentationSession
from squid_layouts.semantic import Item, Items, Paragraph


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


class TestSolvePagination:
    def test_overflowing_paginate_node_produces_pages(self):
        body = "\n".join(f"line {index:04d}" for index in range(1000))
        solved = solve([Heading("Report"), Code(body, overflow=Paginate())])
        assert solved.pages > 1
        assert solved.pager is not None

    def test_footer_is_budgeted_not_guessed(self):
        # Every page, including its footer, fits the real budget: the PAGE_CHARS killer.
        body = "\n".join("z" * 80 for _ in range(200))
        solved = solve([Heading("H" * 200), Code(body, overflow=Paginate())])
        assert solved.pager is not None
        for page in range(solved.pager.pages):
            solved.pager.select(page)
            total = _total_text(solved)
            assert total <= LIMITS.total_text, f"page {page} is {total}"

    def test_fitting_paginate_node_needs_no_pager(self):
        solved = solve([Text("short", overflow=Paginate())])
        assert solved.pager is None
        assert solved.pages == 1

    def test_multiple_paginate_nodes_are_independent(self):
        solved = solve(
            [
                Text("a" * 3000, overflow=Paginate(key="alpha")),
                Text("b" * 3000, overflow=Paginate(key="beta")),
            ],
            page={"alpha": 0, "beta": 1},
        )
        assert [(pager.key, pager.page) for pager in solved.pagers] == [("alpha", 0), ("beta", 1)]


def _total_text(solved) -> int:
    def walk(children) -> int:
        total = 0
        for child in children:
            if isinstance(child, RText):
                total += len(child.content)
            elif hasattr(child, "texts"):
                total += sum(len(text.content) for text in child.texts)
            elif hasattr(child, "children"):
                total += walk(child.children)
        return total

    return walk(solved.children)


class Browser(Component):
    def render(self):
        body = "\n".join(f"entry {index:04d}" for index in range(2000))
        return [Heading("Entries"), Code(body, overflow=Paginate(key="entries"))]


class TwoBrowsers(Component):
    def __init__(self) -> None:
        self.left_version = "old"

    def render(self):
        left = tuple(f"{self.left_version} left {index}" for index in range(30))
        right = tuple(f"right {index}" for index in range(30))
        return [
            Lines(left, overflow=Paginate(key="left", per=10)),
            Lines(right, overflow=Paginate(key="right", per=10)),
        ]


class Catalog(Component):
    """A semantic picker whose list can shift under the reader."""

    def __init__(self) -> None:
        self.lead: tuple[str, ...] = ()

    def render(self):
        keys = (*self.lead, *(str(index) for index in range(36)))
        return [Items("catalog", tuple(Item(key, f"Item {key}", (Paragraph("detail"),)) for key in keys))]


class TestMountPagination:
    def _nav_buttons(self, view) -> list[discord.ui.Button]:
        return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    def test_nav_row_is_synthesized(self):
        mount = Mount(Browser(), timeout=None)
        view = commit_render(mount)
        prev_button, next_button = self._nav_buttons(view)
        assert prev_button.disabled  # first page
        assert not next_button.disabled
        assert_within_limits(view)
        assert conform(view) == []

    async def test_next_advances_and_edges_disable(self):
        mount = Mount(Browser(), timeout=None)
        commit_render(mount)
        interaction = fake_interaction()

        await mount.dispatch("__page_next.entries", interaction)

        edited = interaction.response.edit_message.await_args.kwargs["view"]
        prev_button, _ = self._nav_buttons(edited)
        assert not prev_button.disabled
        footers = [c.content for c in edited.walk_children() if isinstance(c, discord.ui.TextDisplay)]
        assert any("Page 2 of" in text for text in footers)

    def test_the_mount_draws_once_per_render(self, monkeypatch):
        """The fingerprint dance used to make every flush plan twice."""
        from squid_layouts.discord import mount as mount_module

        calls = 0
        original = mount_module.compose

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(mount_module, "compose", counted)
        mount = Mount(TwoBrowsers(), timeout=None)
        commit_render(mount)
        assert calls == 1

    async def test_a_paged_picker_follows_its_anchor_through_the_mount(self):
        """The production path, which used to launder every cursor through `page=`."""
        catalog = Catalog()
        mount = Mount(catalog, timeout=None)
        commit_render(mount)
        await mount.dispatch("__page_next.catalog.items", fake_interaction())
        assert mount.presentation.cursor("catalog.items").index == 1

        catalog.lead = ("new",)
        view = commit_render(mount)

        # One item joined the head, so the reader's page slid by one to keep them on it.
        values = [
            option.value
            for item in view.walk_children()
            if isinstance(item, discord.ui.Select)
            for option in item.options
        ]
        assert "24" in values

    def test_stopping_replaced_view_keeps_new_paginator_registered(self):
        """discord.py must retain the new generation after Mount stops the old one."""
        mount = Mount(Browser(), timeout=None)
        first = commit_render(mount)
        second = commit_render(mount)
        message_id = 42
        store = ViewStore(cast(ConnectionState, SimpleNamespace()))

        store.add_view(first, message_id)
        store.add_view(second, message_id)
        first.stop()

        next_button = next(button for button in self._nav_buttons(second) if button.label == "Next")
        assert (next_button.type.value, next_button.custom_id) in store._views[message_id]

    async def test_a_custom_nav_factory_replaces_the_stock_controls(self):
        def nav(context):
            label = f"{context.page + 1}/{context.pages}"
            return (Row((Button(label=label, on_click=context.on_next, key="jump"),)),)

        mount = Mount(Browser(), timeout=None, nav=nav)
        view = commit_render(mount)
        assert [button.label for button in self._nav_buttons(view)] == [
            f"1/{mount.presentation.cursor('entries').extent}"
        ]

        await mount.dispatch("jump", fake_interaction())

        assert mount.presentation.cursor("entries").index == 1

    async def test_prev_at_first_page_is_a_clean_noop(self):
        mount = Mount(Browser(), timeout=None)
        commit_render(mount)
        interaction = fake_interaction()

        await mount.dispatch("__page_prev.entries", interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_two_pagers_advance_independently(self):
        mount = Mount(TwoBrowsers(), timeout=None)
        commit_render(mount)

        await mount.dispatch("__page_next.left", fake_interaction())

        assert {key: cursor.index for key, cursor in mount.presentation.cursors.items()} == {"left": 1, "right": 0}

    async def test_changed_content_resets_only_its_pager(self):
        component = TwoBrowsers()
        mount = Mount(component, timeout=None)
        commit_render(mount)
        await mount.dispatch("__page_next.left", fake_interaction())
        await mount.dispatch("__page_next.right", fake_interaction())

        component.left_version = "new"
        mount.invalidate()
        commit_render(mount)

        assert {key: cursor.index for key, cursor in mount.presentation.cursors.items()} == {"left": 0, "right": 1}


class TestCountPages:
    def _entries(self, count: int) -> tuple[str, ...]:
        return tuple(f"entry {index}" for index in range(count))

    def test_a_short_list_paginates_by_count_not_by_pressure(self):
        solved = solve([Lines(self._entries(25), overflow=Paginate(per=10))])
        assert solved.pages == 3

    def test_no_entry_is_lost_across_the_pages(self):
        entries = self._entries(25)
        solved = solve([Lines(entries, overflow=Paginate(per=10))])
        assert solved.pager is not None
        assert "\n".join(solved.pager.fragments).split("\n") == list(entries)

    def test_a_list_that_fits_one_page_has_no_pager(self):
        solved = solve([Lines(self._entries(4), overflow=Paginate(per=10))])
        assert solved.pager is None

    def test_an_oversized_count_page_is_split_further_to_fit(self):
        solved = solve([Lines(tuple("x" * 3000 for _ in range(4)), overflow=Paginate(per=2))])
        assert solved.pager is not None
        assert solved.pager.pages > 2
        for page in range(solved.pager.pages):
            solved.pager.select(page)
            assert _total_text(solved) <= LIMITS.total_text

    def test_the_node_footer_overrides_chrome(self):
        solved = solve([Lines(self._entries(25), overflow=Paginate(per=10, footer=lambda p, n: f"{p}/{n} · 25"))])
        assert solved.pager is not None
        assert any("1/3 · 25" in child.content for child in solved.children if isinstance(child, RText))

    def test_count_pages_on_a_non_lines_node_fall_back_to_budget_pages(self):
        solved = solve([Text("short", overflow=Paginate(per=10))])
        assert solved.pager is None
        assert any("not a Lines node" in note for note in solved.notes)

    def test_per_must_be_positive(self):
        with pytest.raises(ValueError, match="at least 1"):
            Paginate(per=0)


class TestSolverNav:
    def _paginated(self) -> list:
        return [Code("\n".join(f"line {index:04d}" for index in range(1000)), overflow=Paginate())]

    def _nav(self, key: str, page: int, pages: int):
        async def move(interaction) -> None: ...

        return default_nav(DEFAULT_CHROME)(PageContext(key=key, page=page, pages=pages, on_prev=move, on_next=move))

    def test_nav_nodes_are_realized_into_the_document(self):
        solved = solve(self._paginated(), nav=self._nav)
        assert isinstance(solved.children[-1], Row)

    def test_an_unpaginated_document_gets_no_nav(self):
        solved = solve([Text("short")], nav=self._nav)
        assert not any(isinstance(child, Row) for child in solved.children)

    def test_the_page_is_clamped_and_reported(self):
        assert solve(self._paginated(), page=999).page == solve(self._paginated()).pages - 1
        assert solve(self._paginated(), page=-5).page == 0

    def test_a_text_bearing_nav_factory_is_rejected(self):
        # `NavNode` rejects this statically; the runtime guard is for callers without a
        # type checker, so the suppression here is the test doing its job.
        with pytest.raises(ValueError, match="component-bearing"):
            solve(self._paginated(), nav=lambda key, page, pages: [Text("page {page}")])  # type: ignore


class TestRepage:
    """Turning a page is a projection: the fit that produced it does not change."""

    def _paginated(self) -> list:
        return [Code("\n".join(f"line {index:04d}" for index in range(1000)), overflow=Paginate(key="lines"))]

    def _nav(self, key: str, page: int, pages: int):
        async def move(interaction) -> None: ...

        return default_nav(DEFAULT_CHROME)(PageContext(key=key, page=page, pages=pages, on_prev=move, on_next=move))

    def test_repage_moves_the_page_without_moving_the_fit(self):
        solved = solve(self._paginated(), nav=self._nav)
        before = solved.components
        pager = solved.pager
        assert pager is not None and solved.pages > 2
        solved.repage({"lines": 2})
        direct = solve(self._paginated(), nav=self._nav, page=2).pager
        assert direct is not None
        assert pager.page == 2
        assert pager.slot.content == direct.slot.content
        assert _component_count(solved.children) == before

    def test_repage_redraws_the_nav_it_replaced(self):
        solved = solve(self._paginated(), nav=self._nav)
        previous = self._previous_button(solved)
        assert previous.disabled  # on page 0
        solved.repage({"lines": 1})
        assert not self._previous_button(solved).disabled

    @staticmethod
    def _previous_button(solved) -> Button:
        row = solved.children[-1]
        assert isinstance(row, Row)
        button = row.items[0]
        assert isinstance(button, Button)
        return button

    def test_repage_clamps_like_the_solver_does(self):
        solved = solve(self._paginated(), nav=self._nav)
        solved.repage({"lines": 999})
        assert solved.page == solved.pages - 1
        solved.repage({"lines": -5})
        assert solved.page == 0

    def test_a_nav_factory_that_hides_controls_between_pages_is_rejected(self):
        def hiding(key: str, page: int, pages: int):
            controls = self._nav(key, page, pages)
            return controls if page else []

        solved = solve(self._paginated(), nav=hiding)
        with pytest.raises(LayoutInvariantError, match="changed shape between pages"):
            solved.repage({"lines": 1})


class TestBuildModal:
    def test_oversized_spec_is_clamped_by_construction(self):
        # The live-bug shape: a default joined from unbounded user data.
        spec = ModalSpec(
            title="Edit Build " + "x" * 100,
            labels=(
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

        spec = ModalSpec(title="T", labels=(LabelSpec(text="Name", input=TextInputSpec(label="n", key="name")),))
        modal = build_modal(spec, on_submit=on_submit)
        next(iter(modal._inputs.values()))._value = "steve"  # pyrefly: ignore

        await modal.on_submit(fake_interaction())

        assert received == {"name": "steve"}


@given(st.text(min_size=4500, max_size=9000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_paginated_documents_fit_on_every_page(body):
    card_node = section(truncate(paragraph("intro")), fields(field("k", "v")), heading="Title")
    lowered = lower_semantics([card_node], limits=LIMITS, chrome=DEFAULT_CHROME, session=PresentationSession()).nodes
    solved = solve([*lowered, Code(body, overflow=Paginate())])
    if solved.pager is None:
        return
    for page in range(solved.pager.pages):
        solved.pager.select(page)
        assert _total_text(solved) <= LIMITS.total_text


@given(st.integers(min_value=0, max_value=60), st.integers(min_value=1, max_value=12))
def test_count_pages_hold_every_entry_exactly_once(count, per):
    entries = tuple(f"entry {index}" for index in range(count))
    solved = solve([Lines(entries, overflow=Paginate(per=per))])
    if solved.pager is None:
        assert count <= per
        return
    assert solved.pager.pages == -(-count // per)
    assert [line for fragment in solved.pager.fragments for line in fragment.split("\n")] == list(entries)


def test_the_solver_counts_the_nav_it_realized():
    # The assertion that replaced NAV_ROW_COMPONENTS: nav is counted because it is realized,
    # so the solver's component budget matches what the built view actually contains.
    async def move(interaction) -> None: ...

    def nav(key: str, page: int, pages: int):
        return default_nav(DEFAULT_CHROME)(PageContext(key=key, page=page, pages=pages, on_prev=move, on_next=move))

    view = commit_render(Mount(Browser(), timeout=None))
    solved = solve(Browser().render(), nav=nav)
    assert _component_count(solved.children) == len(list(view.walk_children()))
