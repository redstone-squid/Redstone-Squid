"""Degradation ladder (alts) tests."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

import squid_layouts as sl
from squid_layouts.discord import (
    conform,
    render_static,
)
from squid_layouts.discord.testing import assert_within_limits
from squid_layouts.planning import SolveNoteCode, measure
from squid_layouts.planning.measure import MeasuredLayout, RPanel, RSection, RText
from squid_layouts.primitives import (
    Alt,
    Condense,
    Lines,
    Never,
    Text,
    alts,
)


def _static_view(*args, **kwargs) -> discord.ui.LayoutView:
    """The drawn layout of a sessionless V2 document, for tests that only read components."""
    return render_static(*args, **kwargs).layout


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def _solved_texts(solved: MeasuredLayout) -> list[str]:
    texts: list[str] = []

    def walk(children) -> None:
        for child in children:
            if isinstance(child, RText):
                texts.append(child.content)
            elif isinstance(child, RSection):
                texts.extend(text.content for text in child.texts)
            elif isinstance(child, RPanel):
                walk(child.children)

    walk(solved.children)
    return texts


class TestAltsPolicy:
    def test_fitting_content_keeps_preferred_form(self):
        view = _static_view([Text("all the links", overflow=alts("3 links"))])
        assert _texts(view) == ["all the links"]

    def test_pressure_picks_the_largest_fitting_alternate(self):
        long_urls = ", ".join(f"https://example.invalid/{index}" for index in range(300))
        node = Text(long_urls, overflow=alts("300 links — first: https://example.invalid/0", "300 links"))
        filler = Text("f" * 3900)
        view = _static_view([filler, node])
        texts = _texts(view)
        assert "300 links" in texts[-1]
        assert "https://exampl" not in texts[-1] or texts[-1].endswith("/0")
        assert_within_limits(view)

    def test_exhausted_ladder_trims_the_last_alternate(self):
        node = Text("x" * 5000, overflow=alts("y" * 4500))
        filler = Text("f" * 3990, priority=10)
        solved = measure([filler, node])
        assert any(note.code is SolveNoteCode.ALTERNATE_EXHAUSTED for note in solved.notes)
        assert sum(map(len, _solved_texts(solved))) <= 4000


class TestLinesLadders:
    def test_largest_entry_degrades_before_any_entry_spills(self):
        entries = (
            "short entry",
            Alt("L" * 3000, ("long entry degraded",)),
            "another short entry",
        )
        filler = Text("f" * 3000, priority=10)
        solved = measure([filler, Lines(entries)])
        text = "\n".join(_solved_texts(solved))
        assert "long entry degraded" in text
        assert "short entry" in text and "another short entry" in text
        assert "more" not in text  # nothing spilled: degrading sufficed

    def test_spill_still_happens_after_ladders_exhaust(self):
        entries = tuple(Alt(f"entry {index} " + "x" * 500, (f"entry {index} " + "y" * 200,)) for index in range(60))
        solved = measure([Lines(entries)])
        texts = _solved_texts(solved)
        assert any("more" in text for text in texts)
        assert sum(map(len, texts)) <= 4000


class TestCondensePolicy:
    def test_entries_step_down_their_ladders_and_none_is_dropped(self):
        # A condensing block is a fixed cost, so its ladders engage only when the block
        # itself overdraws the message rather than merely because a neighbour wants room.
        entries = (
            "short entry",
            Alt("L" * 5000, ("long entry condensed",)),
            "another short entry",
        )
        solved = measure([Lines(entries, overflow=Condense())])
        text = "\n".join(_solved_texts(solved))
        assert "long entry condensed" in text
        assert "short entry" in text and "another short entry" in text
        assert "more" not in text  # Condense never spills a whole entry

    def test_exhausted_ladders_trim_the_joined_block(self):
        entries = tuple(Alt("entry " + "x" * 500, ("entry " + "y" * 400,)) for _ in range(20))
        solved = measure([Lines(entries, overflow=Condense())])
        assert any(note.code is SolveNoteCode.CONDENSE_TRUNCATED for note in solved.notes)
        assert sum(map(len, _solved_texts(solved))) <= 4000

    def test_plain_entries_behave_like_never(self):
        # Nothing to step, so the block trims as a whole rather than losing an entry.
        solved = measure([Lines(tuple("line " + "x" * 500 for _ in range(20)), overflow=Condense())])
        assert not any("more" in text for text in _solved_texts(solved))
        assert sum(map(len, _solved_texts(solved))) <= 4000

    def test_a_condensing_block_is_charged_before_flexible_neighbours(self):
        # The card's shock absorber is the prose, not the field list: the body trims first.
        body = Text("b" * 3000, priority=-5)
        block = Lines(tuple(f"**F{index}:** value" for index in range(10)), overflow=Condense())
        solved = measure([body, block, Text("f" * 2000, priority=-5)])
        texts = _solved_texts(solved)
        assert all(f"**F{index}:** value" in texts[1] for index in range(10))

    def test_never_still_outranks_a_condensing_block(self):
        # A heading may not shrink at all, so it is charged ahead of a block that can.
        heading = Text("H" * 200, overflow=Never())
        block = Lines(tuple(Alt("e" * 900, ("e" * 100,)) for _ in range(6)), overflow=Condense())
        solved = measure([heading, block])
        assert "H" * 200 in _solved_texts(solved)
        assert sum(map(len, _solved_texts(solved))) <= 4000


class TestConstrainedShapes:
    def test_alts_rejects_empty_and_growing_ladders(self):
        with pytest.raises(ValueError, match="at least one step"):
            alts()
        with pytest.raises(ValueError, match="non-empty"):
            alts("ok", "")
        with pytest.raises(ValueError, match="must not grow"):
            alts("short", "much much longer")

    def test_alt_rejects_fallbacks_longer_than_the_primary(self):
        with pytest.raises(ValueError, match="no longer than the primary"):
            Alt("tiny", ("much longer fallback",))

    def test_field_fallbacks_normalize_instead_of_raising(self):
        # A caller-supplied fallback that came out longer than its primary is skipped, not fatal.
        node = sl.section(sl.fields(sl.field("Creators", "a, b", fallbacks=("a and 3 others",))), heading="T")
        view = _static_view([node])
        assert any("a, b" in text for text in _texts(view))


class TestCardFieldLadders:
    def test_url_field_degrades_meaningfully(self):
        urls = [f"https://example.invalid/video-{index}" for index in range(150)]
        node = sl.section(
            sl.truncate(sl.paragraph("d" * 3500)),
            sl.section(
                sl.fields(
                    sl.field(
                        "Videos",
                        ", ".join(urls),
                        fallbacks=(f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links"),
                    ),
                ),
                heading="Resources",
            ),
            heading="Build",
        )
        view = _static_view([node])
        text = "\n".join(_texts(view))
        assert "**Videos:**" in text
        assert "150 links" in text
        assert_within_limits(view)
        assert conform(view) == []


@given(
    body=st.text(max_size=5000),
    ladder=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=4),
)
def test_alts_documents_always_fit(body: str, ladder: list[str]):
    ladder = sorted(ladder, key=len, reverse=True)
    view = _static_view([Text("pad " * 400), Text(body or "x", overflow=alts(*ladder))])
    assert_within_limits(view)
    assert conform(view) == []
