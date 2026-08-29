"""Preset shape and degradation tests."""

from hypothesis import given
from hypothesis import strategies as st

from squid_layouts.discord import (
    conform,
    render_static,
)
from squid_layouts.discord.testing import assert_within_limits
from squid_layouts.primitives import (
    FieldGroup,
    banner,
    card,
    listing,
    report,
)
from squid_layouts.primitives.presets import Field


def _texts(view) -> list[str]:
    def flat(components):
        for component in components:
            yield component
            yield from flat(component.get("components", []))
            if component.get("accessory"):
                yield component["accessory"]

    return [c["content"] for c in flat(view.to_components()) if c.get("type") == 10]


class TestCard:
    def test_full_card(self):
        view = render_static(
            [
                card(
                    "Build 42",
                    "A compact door.",
                    fields=(Field("Author", "steve"),),
                    groups=(FieldGroup("Meta", (Field("Size", "3x3"),)),),
                    footer="pending review",
                    media=("https://example.invalid/a.png", "https://example.invalid/b.png"),
                    accent=0x43B581,
                )
            ]
        )
        texts = _texts(view)
        assert any(t.startswith("## Build 42") for t in texts)
        assert any("**Author**\nsteve" in t for t in texts)
        assert any("### Meta" in t and "**Size:** 3x3" in t for t in texts)
        assert any(t.startswith("-# pending review") for t in texts)
        assert conform(view) == []
        assert_within_limits(view)

    def test_long_body_no_longer_starves_the_fields(self):
        # The historic card_container flaw inverted: fields survive, the body trims.
        view = render_static(
            [
                card(
                    "Title",
                    "b" * 5000,
                    fields=tuple(Field(f"F{index}", "v" * 40) for index in range(10)),
                    footer="footer",
                )
            ]
        )
        texts = _texts(view)
        assert any("**F9**" in t for t in texts)
        assert any(t.startswith("-# footer") for t in texts)
        assert_within_limits(view)

    def test_many_groups_spill_instead_of_overflowing(self):
        groups = tuple(
            FieldGroup(f"Group {index}", tuple(Field(f"n{j}", "v" * 80) for j in range(6))) for index in range(30)
        )
        view = render_static([card("Directory", groups=groups)])
        assert any("more" in t for t in _texts(view))
        assert_within_limits(view)


def test_banner_plain_and_accented():
    assert _texts(render_static([banner("hello")])) == ["hello"]
    accented = render_static([banner("hello", accent=0xF04747)])
    assert accented.to_components()[0]["type"] == 17


def test_listing_spills():
    view = render_static([listing("Records", tuple(f"record {index} " + "x" * 100 for index in range(100)))])
    texts = _texts(view)
    assert any("record 0" in t for t in texts)
    assert any("more" in t for t in texts)
    assert_within_limits(view)


def test_report_fences_the_body():
    view = render_static([report("Crash", "Traceback...\nboom", lang="py", fields=(Field("Origin", "vote"),))])
    texts = _texts(view)
    assert any(t.startswith("```py\n") for t in texts)
    assert_within_limits(view)


@given(
    title=st.text(min_size=1, max_size=500),
    description=st.one_of(st.none(), st.text(max_size=6000)),
    fields=st.lists(st.tuples(st.text(min_size=1, max_size=200), st.text(max_size=400)), max_size=30),
    footer=st.one_of(st.none(), st.text(max_size=600)),
)
def test_any_card_fits(title, description, fields, footer):
    node = card(title, description, fields=tuple(Field(n, v) for n, v in fields), footer=footer)
    view = render_static([node])
    assert_within_limits(view)
    assert conform(view) == []
