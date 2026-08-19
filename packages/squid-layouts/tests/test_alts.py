"""Degradation ladder (alts) tests."""

import discord
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    Field,
    FieldGroup,
    Lines,
    Text,
    alts,
    assert_within_limits,
    card,
    conform,
    materialize,
    render_static,
    solve,
)


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


class TestAltsPolicy:
    def test_fitting_content_keeps_preferred_form(self):
        view = render_static([Text("all the links", overflow=alts("3 links"))])
        assert _texts(view) == ["all the links"]

    def test_pressure_picks_the_largest_fitting_alternate(self):
        long_urls = ", ".join(f"https://example.invalid/{index}" for index in range(300))
        node = Text(long_urls, overflow=alts("300 links — first: https://example.invalid/0", "300 links"))
        filler = Text("f" * 3900)
        view = render_static([filler, node])
        texts = _texts(view)
        assert "300 links" in texts[-1]
        assert "https://exampl" not in texts[-1] or texts[-1].endswith("/0")
        assert_within_limits(view)

    def test_exhausted_ladder_trims_the_last_alternate(self):
        node = Text("x" * 5000, overflow=alts("y" * 4500))
        filler = Text("f" * 3990, priority=10)
        solved = solve([filler, node])
        assert any("exhausted" in note for note in solved.notes)
        assert_within_limits(materialize(solved))


class TestLinesLadders:
    def test_largest_entry_degrades_before_any_entry_spills(self):
        entries = (
            ("short entry",),
            ("L" * 3000, "long entry degraded"),
            ("another short entry",),
        )
        filler = Text("f" * 3000, priority=10)
        solved = solve([filler, Lines(entries)])
        view = materialize(solved)
        text = "\n".join(_texts(view))
        assert "long entry degraded" in text
        assert "short entry" in text and "another short entry" in text
        assert "more" not in text  # nothing spilled: degrading sufficed

    def test_spill_still_happens_after_ladders_exhaust(self):
        entries = tuple((f"entry {index} " + "x" * 500, f"entry {index} " + "y" * 200) for index in range(60))
        solved = solve([Lines(entries)])
        view = materialize(solved)
        assert any("more" in text for text in _texts(view))
        assert_within_limits(view)


class TestCardFieldLadders:
    def test_url_field_degrades_meaningfully(self):
        urls = [f"https://example.invalid/video-{index}" for index in range(150)]
        node = card(
            "Build",
            "d" * 3500,
            groups=(
                FieldGroup(
                    "Resources",
                    (
                        Field(
                            "Videos",
                            ", ".join(urls),
                            alts=(f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links"),
                        ),
                    ),
                ),
            ),
        )
        view = render_static([node])
        text = "\n".join(_texts(view))
        assert "**Videos:**" in text
        assert "150 links" in text
        assert_within_limits(view)
        assert conform(view) == []


@given(
    body=st.text(max_size=5000),
    ladder=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=4),
)
def test_alts_documents_always_fit(body, ladder):
    view = render_static([Text("pad " * 400), Text(body or "x", overflow=alts(*ladder))])
    assert_within_limits(view)
    assert conform(view) == []
