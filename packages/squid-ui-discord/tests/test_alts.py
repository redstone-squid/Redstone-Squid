"""Text alternatives tried from most to least detailed when the message runs out of space."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import squid_ui as sl
from squid_ui.authoring.presentations import Shortening
from squid_ui.ext.discord import (
    conform,
)
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.primitives import Alt, Condense, Lines, Never, Text, alts
from squid_ui.ext.discord.testing import assert_within_limits
from squid_ui.planning import plan
from squid_ui.planning.result import PlanResult


def _planned_texts(result: PlanResult[discord_scene.ComponentsV2]) -> list[str]:
    return [node.content for node in result.scene.body.children if isinstance(node, discord_scene.Text)]


class TestAltsPolicy:
    def test_fitting_content_keeps_preferred_form(self):
        view = sd.static_view([Text("all the links", overflow=alts("3 links"))])
        assert sd.payload_texts(view) == ["all the links"]

    def test_pressure_picks_the_largest_fitting_alternate(self):
        long_urls = ", ".join(f"https://example.invalid/{index}" for index in range(300))
        node = Text(long_urls, overflow=alts("300 links — first: https://example.invalid/0", "300 links"))
        filler = Text("f" * 3900)
        view = sd.static_view([filler, node])
        texts = sd.payload_texts(view)
        assert "300 links" in texts[-1]
        assert "https://exampl" not in texts[-1] or texts[-1].endswith("/0")
        assert_within_limits(view)

    def test_exhausted_ladder_trims_the_last_alternate(self):
        node = Text("x" * 5000, overflow=alts("y" * 4500))
        filler = Text("f" * 3990, priority=10)
        solved = plan([filler, node], target=sd.target_profile("test"))
        assert any(isinstance(effect, Shortening) for effect in solved.effects)
        assert sum(map(len, _planned_texts(solved))) <= 4000


class TestLinesLadders:
    def test_largest_entry_degrades_before_any_entry_spills(self):
        entries = (
            "short entry",
            Alt("L" * 3000, ("long entry degraded",)),
            "another short entry",
        )
        filler = Text("f" * 3000, priority=10)
        solved = plan([filler, Lines(entries)], target=sd.target_profile("test"))
        text = "\n".join(_planned_texts(solved))
        assert "long entry degraded" in text
        assert "short entry" in text and "another short entry" in text
        assert "more" not in text  # nothing spilled: degrading sufficed

    def test_spill_still_happens_after_ladders_exhaust(self):
        entries = tuple(Alt(f"entry {index} " + "x" * 500, (f"entry {index} " + "y" * 200,)) for index in range(60))
        solved = plan([Lines(entries)], target=sd.target_profile("test"))
        texts = _planned_texts(solved)
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
        solved = plan([Lines(entries, overflow=Condense())], target=sd.target_profile("test"))
        text = "\n".join(_planned_texts(solved))
        assert "long entry condensed" in text
        assert "short entry" in text and "another short entry" in text
        assert "more" not in text  # Condense never spills a whole entry

    def test_exhausted_ladders_trim_the_joined_block(self):
        entries = tuple(Alt("entry " + "x" * 2000, ("entry " + "y" * 1500,)) for _ in range(3))
        # This tests shortening after all replacements, independently of search exhaustion.
        solved = plan([Lines(entries, overflow=Condense())], target=sd.target_profile("test"), search_budget=100_000)
        assert any(isinstance(effect, Shortening) for effect in solved.effects)
        assert sum(map(len, _planned_texts(solved))) <= 4000

    def test_plain_entries_behave_like_never(self):
        # Nothing to step, so the block trims as a whole rather than losing an entry.
        solved = plan(
            [Lines(tuple("line " + "x" * 2000 for _ in range(3)), overflow=Condense())],
            target=sd.target_profile("test"),
            search_budget=100_000,
        )
        assert not any("more" in text for text in _planned_texts(solved))
        assert sum(map(len, _planned_texts(solved))) <= 4000

    def test_never_still_outranks_a_condensing_block(self):
        # A heading may not shrink at all, so it is charged ahead of a block that can.
        heading = Text("H" * 200, overflow=Never())
        block = Lines(tuple(Alt("e" * 900, ("e" * 100,)) for _ in range(6)), overflow=Condense())
        solved = plan([heading, block], target=sd.target_profile("test"))
        assert "H" * 200 in _planned_texts(solved)
        assert sum(map(len, _planned_texts(solved))) <= 4000


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
        node = sl.section(sl.heading("T"), sl.fields(sl.field("Creators", "a, b", fallbacks=("a and 3 others",))))
        view = sd.static_view([node])
        assert any("a, b" in text for text in sd.payload_texts(view))


class TestCardFieldLadders:
    def test_url_field_degrades_meaningfully(self):
        urls = [f"https://example.invalid/video-{index}" for index in range(150)]
        node = sl.section(
            sl.heading("Build"),
            sl.truncate(sl.paragraph("d" * 3500)),
            sl.section(
                sl.heading("Resources"),
                sl.fields(
                    sl.field(
                        "Videos",
                        ", ".join(urls),
                        fallbacks=(f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links"),
                    ),
                ),
            ),
        )
        view = sd.static_view([node])
        text = "\n".join(sd.payload_texts(view))
        assert "Videos:" in text
        assert "150 links" in text
        assert_within_limits(view)
        assert conform(view) == []


@settings(deadline=None)
@given(
    body=st.text(max_size=5000),
    ladder=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=4),
)
def test_alts_documents_always_fit(body: str, ladder: list[str]):
    ladder = sorted(ladder, key=len, reverse=True)
    view = sd.static_view([Text("pad " * 400), Text(body or "x", overflow=alts(*ladder))])
    assert_within_limits(view)
    assert conform(view) == []
