"""Unit and property tests for the concrete-layout evaluator."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import LayoutInvariantError
from squid_layouts.discord import (
    ELLIPSIS,
    conform,
    render_static,
)
from squid_layouts.discord import (
    V2_LIMITS as LIMITS,
)
from squid_layouts.discord.testing import assert_within_limits
from squid_layouts.planning import (
    LayoutOverflowError,
    SolveNoteCode,
    measure,
)
from squid_layouts.primitives import (
    Code,
    Drop,
    Footer,
    Heading,
    Lines,
    LinkButton,
    MediaCollection,
    Never,
    Panel,
    RawItem,
    Row,
    Section,
    Sep,
    Spill,
    Text,
    Thumbnail,
    Truncate,
    Variants,
)


def _static_view(*args, **kwargs) -> discord.ui.LayoutView:
    """The drawn layout of a sessionless V2 document, for tests that only read components."""
    return render_static(*args, **kwargs).layout


def _text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(c["content"] for c in _flat(view.to_components()) if c.get("type") == 10)


def _flat(components):
    for component in components:
        yield component
        yield from _flat(component.get("components", []))
        if component.get("accessory"):
            yield component["accessory"]


class TestFitting:
    def test_small_document_renders_verbatim(self):
        view = _static_view(
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
        view = _static_view([Code(content, lang=lang)])
        assert ELLIPSIS not in _text_of(view)
        assert conform(view) == []

    def test_one_char_over_budget_trims(self):
        lang = "py"
        fence_cost = len(f"```{lang}\n") + len("\n```")
        content = "x" * (LIMITS.total_text - fence_cost + 1)
        view = _static_view([Code(content, lang=lang)])
        assert ELLIPSIS in _text_of(view)
        assert conform(view) == []

    def test_priority_orders_the_allocation(self):
        # Low-priority footer shrinks before the body loses a character.
        body = Text("b" * 3900)
        footer = Footer("f" * 400)
        solved = measure([body, footer])
        rendered = [child.content for child in solved.children]  # pyrefly: ignore
        assert rendered[0] == "b" * 3900
        assert len(rendered[1]) <= LIMITS.total_text - 3900

    def test_equal_priority_nodes_share_proportionally(self):
        first = Text("a" * 6000)
        second = Text("b" * 2000)
        solved = measure([first, second])
        lengths = [len(child.content) for child in solved.children]  # pyrefly: ignore
        assert sum(lengths) <= LIMITS.total_text
        # Need ratio is 3:1, so the later node keeps ~1000 chars instead of starving at 0.
        assert lengths[1] >= 900

    def test_dropped_node_refunds_its_budget(self):
        # The Drop node cannot fit, so the Truncate node should get everything back.
        keeper = Text("k" * 3999)
        dropper = Text("d" * 500, overflow=Drop(), priority=-1)
        solved = measure([keeper, dropper])
        assert [child.content for child in solved.children] == ["k" * 3999]  # pyrefly: ignore
        assert any(note.code is SolveNoteCode.NODE_DROPPED for note in solved.notes)

    def test_never_wins_over_higher_priority_flexible_nodes(self):
        pinned = Text("p" * 3500, overflow=Never(), priority=-100)
        flexible = Text("f" * 3500, priority=100)
        solved = measure([pinned, flexible])
        contents = [child.content for child in solved.children]  # pyrefly: ignore
        assert contents[0] == "p" * 3500
        assert len(contents[1]) == LIMITS.total_text - 3500

    def test_an_unresolved_alternative_is_a_planner_bug(self):
        # measure() evaluates one concrete layout; choosing between rungs is plan()'s job.
        with pytest.raises(LayoutInvariantError, match="resolved before measuring"):
            measure([Variants.of(Text("rich"), Text("plain"))])

    def test_an_unresolved_alternative_is_rejected_inside_a_container(self):
        with pytest.raises(LayoutInvariantError, match="resolved before measuring"):
            measure([Panel((Variants.of(Text("rich"), Text("plain")),))])

    def test_unsatisfiable_never_raises_in_strict_mode(self):
        with pytest.raises(LayoutOverflowError):
            measure([Text("x" * 5000, overflow=Never())], strict=True)

    def test_unsatisfiable_never_clamps_outside_strict_mode(self):
        solved = measure([Text("x" * 5000, overflow=Never())])
        assert len(solved.children[0].content) <= LIMITS.total_text  # pyrefly: ignore
        assert solved.failures[0].code is SolveNoteCode.NEVER_BUDGET
        assert isinstance(solved.failures[0].code, str)

    def test_truncate_tail_keeps_the_end(self):
        view = _static_view([Text("start " + "x" * 4000 + " end", overflow=Truncate(keep="tail"))])
        text = _text_of(view)
        assert text.startswith(ELLIPSIS)
        assert text.endswith(" end")

    def test_spill_line_appears_with_count(self):
        lines = tuple(f"entry {index:03d} " + "x" * 90 for index in range(60))
        view = _static_view([Lines(lines)])
        text = _text_of(view)
        assert "entry 000" in text
        assert "more" in text
        assert conform(view) == []

    def test_spill_fits_everything_when_it_can(self):
        view = _static_view([Lines(("a", "b", "c"))])
        assert _text_of(view) == "a\nb\nc"

    def test_code_fences_cannot_be_broken_out_of(self):
        view = _static_view([Code("evil\n```\n@everyone")])
        assert "\n```\n@everyone" not in _text_of(view)

    def test_empty_nodes_vanish(self):
        view = _static_view([Text(""), Lines(()), Text("real")])
        assert _text_of(view) == "real"

    def test_raw_item_text_cost_reserves_budget(self):
        raw = RawItem(factory=lambda: discord.ui.TextDisplay("r" * 100), text_cost=100)
        solved = measure([Text("x" * 4000), raw])
        assert len(solved.children[0].content) <= LIMITS.total_text - 100  # pyrefly: ignore


class TestStructure:
    def test_full_card_shape(self):
        view = _static_view(
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
        types = [c["type"] for c in _flat(payload)]
        assert types.count(12) == 2  # semantic media collection lowered to two valid galleries
        assert conform(view) == []
        assert_within_limits(view)

    def test_oversized_section_keeps_three_texts(self):
        section = Section(
            texts=(Text("a"), Text("b"), Text("c"), Text("d")),
            accessory=Thumbnail("https://example.invalid/a.png"),
        )
        with pytest.raises(LayoutInvariantError, match="section has 4 text slots"):
            render_static([section])

    def test_emptied_section_drops_whole(self):
        section = Section(texts=(Text(""),), accessory=Thumbnail("https://example.invalid/a.png"))
        solved = measure([section])
        assert solved.children == []


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


@given(documents())
def test_rendered_documents_always_fit(nodes):
    view = _static_view(nodes)
    assert_within_limits(view)


@given(documents())
def test_measurement_needs_no_conform_interventions(nodes):
    # The engine must measure exactly: the boundary gate should never have to intervene.
    measure(nodes)
    view = _static_view(nodes)
    assert conform(view) == []
