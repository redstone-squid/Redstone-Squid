"""Engine pagination and ModalSpec tests."""

import discord
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    LIMITS,
    Code,
    Component,
    Field,
    Heading,
    LabelSpec,
    ModalSpec,
    Mount,
    Paginate,
    Text,
    TextInputSpec,
    assert_within_limits,
    build_modal,
    card,
    conform,
    solve,
)
from squid_layouts.solve import split_pages
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

    def test_second_paginate_node_degrades_to_truncate(self):
        solved = solve(
            [Text("a" * 3000, overflow=Paginate()), Text("b" * 3000, overflow=Paginate())],
        )
        assert any("degraded to Truncate" in note for note in solved.notes)


def _total_text(solved) -> int:
    from squid_layouts.materialize import materialize

    view = materialize(solved)
    return sum(len(c.content) for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


class Browser(Component):
    def render(self):
        body = "\n".join(f"entry {index:04d}" for index in range(2000))
        return [Heading("Entries"), Code(body, overflow=Paginate())]


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

        await mount.dispatch("__page_next", interaction)

        edited = interaction.response.edit_message.await_args.kwargs["view"]
        prev_button, _ = self._nav_buttons(edited)
        assert not prev_button.disabled
        footers = [c.content for c in edited.walk_children() if isinstance(c, discord.ui.TextDisplay)]
        assert any("Page 2 of" in text for text in footers)

    async def test_prev_at_first_page_is_a_clean_noop(self):
        mount = Mount(Browser(), timeout=None)
        mount.build_view()
        interaction = fake_interaction()

        await mount.dispatch("__page_prev", interaction)

        interaction.response.defer.assert_awaited_once()


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
