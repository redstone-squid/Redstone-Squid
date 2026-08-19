"""Engine pagination and ModalSpec tests."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    DEFAULT_CHROME,
    LIMITS,
    Button,
    Code,
    Component,
    Field,
    Heading,
    LabelSpec,
    Lines,
    ModalSpec,
    Mount,
    PageContext,
    Paginate,
    Row,
    Text,
    TextInputSpec,
    assert_within_limits,
    build_modal,
    card,
    conform,
    default_nav,
    solve,
)
from squid_layouts.solve import RText, _component_count, split_pages
from squid_layouts.testing import fake_interaction


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
    from squid_layouts.materialize import materialize

    view = materialize(solved)
    return sum(len(c.content) for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


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


class TestMountPagination:
    def _nav_buttons(self, view) -> list[discord.ui.Button]:
        return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    def test_nav_row_is_synthesized(self):
        mount = Mount(Browser(), timeout=None)
        view = mount.build_view()
        prev_button, next_button = self._nav_buttons(view)
        assert prev_button.disabled  # first page
        assert not next_button.disabled
        assert_within_limits(view)
        assert conform(view) == []

    async def test_next_advances_and_edges_disable(self):
        mount = Mount(Browser(), timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("__page_next.entries", interaction)

        edited = interaction.response.edit_message.await_args.kwargs["view"]
        prev_button, _ = self._nav_buttons(edited)
        assert not prev_button.disabled
        footers = [c.content for c in edited.walk_children() if isinstance(c, discord.ui.TextDisplay)]
        assert any("Page 2 of" in text for text in footers)

    async def test_a_custom_nav_factory_replaces_the_stock_controls(self):
        def nav(context):
            label = f"{context.page + 1}/{context.pages}"
            return (Row((Button(label=label, on_click=context.on_next, key="jump"),)),)

        mount = Mount(Browser(), timeout=None, nav=nav)
        view = mount.build_view()
        assert [button.label for button in self._nav_buttons(view)] == [f"1/{mount._pages['entries']}"]

        await mount.dispatch("jump", fake_interaction())

        assert mount._page["entries"] == 1

    async def test_prev_at_first_page_is_a_clean_noop(self):
        mount = Mount(Browser(), timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("__page_prev.entries", interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_two_pagers_advance_independently(self):
        mount = Mount(TwoBrowsers(), timeout=None)
        mount.build_view()

        await mount.dispatch("__page_next.left", fake_interaction())

        assert mount._page == {"left": 1, "right": 0}

    async def test_changed_content_resets_only_its_pager(self):
        component = TwoBrowsers()
        mount = Mount(component, timeout=None)
        mount.build_view()
        await mount.dispatch("__page_next.left", fake_interaction())
        await mount.dispatch("__page_next.right", fake_interaction())

        component.left_version = "new"
        mount.invalidate()
        mount.build_view()

        assert mount._page == {"left": 0, "right": 1}


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
        with pytest.raises(ValueError, match="component-bearing"):
            solve(self._paginated(), nav=lambda key, page, pages: [Text("page {page}")])


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
    solved = solve([card("Title", "intro", fields=(Field("k", "v"),)), Code(body, overflow=Paginate())])
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

    view = Mount(Browser(), timeout=None).build_view()
    solved = solve(Browser().render(), nav=nav)
    assert _component_count(solved.children) == len(list(view.walk_children()))
