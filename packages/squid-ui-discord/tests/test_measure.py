"""Native document rendering and protocol-fit properties."""

import discord
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from squid_ui.ext.discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui.ext.discord import conform, render_static
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.conformance import ELLIPSIS
from squid_ui.ext.discord.testing import assert_within_limits
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.primitives import (
    Code,
    Drop,
    Footer,
    Heading,
    Lines,
    LinkButton,
    MediaCollection,
    Never,
    Panel,
    Row,
    Section,
    Sep,
    Spill,
    Text,
    Thumbnail,
    Truncate,
)


def _text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(sd.payload_texts(view))


class TestFitting:
    def test_small_document_renders_verbatim(self):
        view = sd.static_view(
            [
                Heading("Build 123"),
                Text("A very nice piston door."),
                Sep(),
                Footer("submitted yesterday"),
            ]
        )
        text = _text_of(view)
        assert "## Build 123" in text
        assert "A very nice piston door." in text
        assert "-# submitted yesterday" in text
        assert conform(view) == []

    def test_chrome_is_charged_exactly(self):
        # A code block whose content exactly fills the budget minus its fences must not trim.
        lang = "py"
        fence_cost = len(f"```{lang}\n") + len("\n```")
        content = "x" * (LIMITS.total_text - fence_cost)
        view = sd.static_view([Code(content, lang=lang)])
        assert ELLIPSIS not in _text_of(view)
        assert conform(view) == []

    def test_one_char_over_budget_trims(self):
        lang = "py"
        fence_cost = len(f"```{lang}\n") + len("\n```")
        content = "x" * (LIMITS.total_text - fence_cost + 1)
        view = sd.static_view([Code(content, lang=lang)])
        assert ELLIPSIS in _text_of(view)
        assert conform(view) == []

    def test_truncate_tail_keeps_the_end(self):
        view = sd.static_view([Text("start " + "x" * 4000 + " end", overflow=Truncate(keep="tail"))])
        text = _text_of(view)
        assert text.startswith(ELLIPSIS)
        assert text.endswith(" end")

    def test_spill_line_appears_with_count(self):
        lines = tuple(f"entry {index:03d} " + "x" * 90 for index in range(60))
        view = sd.static_view([Lines(lines)])
        text = _text_of(view)
        assert "entry 000" in text
        assert "more" in text
        assert conform(view) == []

    def test_spill_fits_everything_when_it_can(self):
        view = sd.static_view([Lines(("a", "b", "c"))])
        assert _text_of(view) == "a\nb\nc"

    def test_code_fences_cannot_be_broken_out_of(self):
        view = sd.static_view([Code("evil\n```\n@everyone")])
        assert "\n```\n@everyone" not in _text_of(view)

    def test_empty_nodes_vanish(self):
        view = sd.static_view([Text(""), Lines(()), Text("real")])
        assert _text_of(view) == "real"


class TestStructure:
    def test_full_card_shape(self):
        view = sd.static_view(
            [
                Panel(
                    children=(
                        Section(
                            texts=(Heading("Title"), Text("Body")),
                            accessory=Thumbnail("https://example.invalid/a.png"),
                        ),
                        Sep(),
                        MediaCollection(tuple(f"https://example.invalid/{index}.png" for index in range(12))),
                        Row((LinkButton("Open", "https://example.invalid"),)),
                    ),
                    accent=0x00FF00,
                )
            ]
        )
        payload = view.to_components()
        assert payload[0]["type"] == 17
        types = [c["type"] for c in sd.iter_component_payloads(payload)]
        assert types.count(12) == 2  # semantic media collection lowered to two valid galleries
        assert conform(view) == []
        assert_within_limits(view)

    def test_oversized_section_keeps_three_texts(self):
        section = Section(
            texts=(Text("a"), Text("b"), Text("c"), Text("d")),
            accessory=Thumbnail("https://example.invalid/a.png"),
        )
        with pytest.raises(JointInfeasibleError):
            render_static([section])


# --- Property tests ------------------------------------------------------------------------

_policies = st.sampled_from([Truncate(), Truncate(keep="tail"), Spill(), Drop(), Never()])
_content = st.text(max_size=1500)
_priority = st.integers(min_value=-10, max_value=10)


@st.composite
def documents(draw) -> list:
    nodes = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        kind = draw(st.sampled_from(["text", "heading", "footer", "code", "lines", "sep", "panel", "section"]))
        node = _draw_node(draw, kind)
        if node is not None:
            nodes.append(node)
    return nodes


def _draw_node(draw, kind: str):
    if kind == "text":
        return Text(draw(_content), overflow=draw(_policies), priority=draw(_priority))
    if kind == "heading":
        return Heading(draw(_content), priority=draw(_priority))
    if kind == "footer":
        return Footer(draw(_content))
    if kind == "code":
        return Code(draw(_content), lang=draw(st.sampled_from(["", "py", "json"])))
    if kind == "lines":
        return Lines(tuple(draw(st.lists(st.text(min_size=1, max_size=120), max_size=40))))
    if kind == "sep":
        return Sep(large=draw(st.booleans()))
    if kind == "panel":
        inner = [Text(draw(_content)), Sep()]
        return Panel(children=tuple(inner))
    if kind == "section":
        texts = tuple(Text(draw(st.text(min_size=1, max_size=200))) for _ in range(draw(st.integers(1, 3))))
        return Section(texts=texts, accessory=Thumbnail("https://example.invalid/a.png"))
    return None


@settings(deadline=None)
@given(documents())
def test_rendered_documents_fit_without_conform_interventions(nodes: list) -> None:
    view = sd.static_view(nodes)
    # Check serialization before conform(), which can repair the view in place.
    assert_within_limits(view)
    assert conform(view) == []
