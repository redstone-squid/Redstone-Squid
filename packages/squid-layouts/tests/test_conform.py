"""Unit and property tests for the conform boundary gate."""

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts.discord import LimitViolationError, conform
from squid_layouts.discord.conformance import ELLIPSIS, conform_modal, trim
from squid_layouts.discord import (
    V2_LIMITS as LIMITS,
)
from squid_layouts.discord.testing import assert_within_limits, payload_problems


def _view(*items: discord.ui.Item) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    for item in items:
        view.add_item(item)
    return view


def _row(*items) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


class TestTrim:
    def test_short_text_untouched(self):
        assert trim("abc", 5) == "abc"

    def test_cut_gets_ellipsis_within_limit(self):
        result = trim("a" * 100, 10)
        assert len(result) <= 10
        assert result.endswith(ELLIPSIS)

    def test_degenerate_limits(self):
        assert trim("abc", 1) == ELLIPSIS
        assert trim("abc", 0) == ""

    def test_no_trailing_whitespace_before_marker(self):
        assert trim("word      tail", 8) == f"word{ELLIPSIS}"


class TestConformView:
    def test_clean_view_returns_no_interventions(self):
        view = _view(discord.ui.TextDisplay("hello"), _row(discord.ui.Button(label="ok")))
        assert conform(view) == []
        assert_within_limits(view)

    def test_strict_raises_on_violation(self):
        view = _view(discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)))
        with pytest.raises(LimitViolationError):
            conform(view, strict=True)

    def test_total_text_budget_trims_later_nodes_first(self):
        first = discord.ui.TextDisplay("a" * 3000)
        second = discord.ui.TextDisplay("b" * 3000)
        view = _view(first, second)
        assert conform(view)
        assert first.content == "a" * 3000
        assert len(first.content) + len(second.content) <= LIMITS.total_text
        assert_within_limits(view)

    def test_no_text_display_is_emptied(self):
        displays = [discord.ui.TextDisplay("x" * 2000) for _ in range(4)]
        view = _view(*displays)
        conform(view)
        assert all(len(td.content) >= 1 for td in displays)
        assert_within_limits(view)

    def test_button_label_clamped(self):
        button = discord.ui.Button(label="b" * 200)
        view = _view(_row(button))
        assert conform(view)
        assert button.label is not None and len(button.label) <= LIMITS.button_label
        assert_within_limits(view)

    def test_select_options_and_strings_clamped(self):
        select = discord.ui.Select(placeholder="p" * 300)
        # The options *setter* skips append_option's count check, like a bulk constructor would.
        select.options = [
            discord.SelectOption(label="l" * 150, value=f"{index}" + "v" * 150, description="d" * 150)
            for index in range(30)
        ]
        view = _view(_row(select))
        assert conform(view)
        assert len(select.options) <= LIMITS.select_options
        assert all(len(o.label) <= LIMITS.option_label for o in select.options)
        assert all(len(o.value) <= LIMITS.option_value for o in select.options)
        assert_within_limits(view)

    def test_option_values_stay_unique_enough_to_dispatch(self):
        # Values are cut without a marker; the index prefix keeps them distinct after the cut.
        select = discord.ui.Select()
        select.options = [discord.SelectOption(label="x", value=f"{index}:" + "v" * 150) for index in range(5)]
        view = _view(_row(select))
        conform(view)
        values = [o.value for o in select.options]
        assert len(set(values)) == len(values)

    def test_gallery_clamped(self):
        # The items setter enforces the 10 cap, so overflow can only come from drift or
        # internal-state bypass; simulate the bypass.
        gallery = discord.ui.MediaGallery()
        gallery._underlying.items = [
            discord.MediaGalleryItem("https://example.invalid/a.png", description="d" * 300) for _ in range(15)
        ]
        view = _view(gallery)
        assert conform(view)
        assert len(gallery.items) <= LIMITS.gallery_items
        assert_within_limits(view)

    def test_component_count_overflow_is_reported_not_clamped(self):
        # add_item enforces the 40 cap across nesting, so reach an overflow by bypassing it.
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        view.add_item(container)
        container._children.extend(discord.ui.TextDisplay("x") for _ in range(45))
        interventions = conform(view)
        assert any("components exceed" in note for note in interventions)


class TestConformModal:
    def test_oversized_modal_is_clamped(self):
        modal = discord.ui.Modal(title="t" * 100, timeout=None)
        modal.add_item(
            discord.ui.Label(
                text="l" * 100,
                description="d" * 200,
                component=discord.ui.TextInput(label="i", default="v" * 5000, placeholder="p" * 200),
            )
        )
        assert conform_modal(modal)
        assert_within_limits(modal)

    def test_default_respects_declared_max_length(self):
        text_input = discord.ui.TextInput(label="i", default="v" * 50, max_length=10)
        modal = discord.ui.Modal(title="t", timeout=None)
        modal.add_item(discord.ui.Label(text="l", component=text_input))
        assert conform_modal(modal)
        assert text_input.default is not None and len(text_input.default) <= 10


# --- Property tests ------------------------------------------------------------------------

_texts = st.lists(st.text(min_size=1, max_size=3000), max_size=5)
_labels = st.lists(st.text(max_size=200), max_size=4)
_options = st.lists(
    st.tuples(st.text(min_size=1, max_size=150), st.text(max_size=150)),
    max_size=30,
)


@st.composite
def views(draw) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    for content in draw(_texts):
        view.add_item(discord.ui.TextDisplay(content))
    button_labels = draw(_labels)
    if button_labels:
        view.add_item(
            _row(*(discord.ui.Button(label=label or None, emoji=None if label else "x") for label in button_labels))
        )
    option_specs = draw(_options)
    if option_specs:
        select = discord.ui.Select(placeholder=draw(st.one_of(st.none(), st.text(max_size=300))))
        select.options = [
            discord.SelectOption(label=label, value=f"{index}:{label}"[:150], description=description or None)
            for index, (label, description) in enumerate(option_specs)
        ]
        view.add_item(_row(select))
    return view


@given(views())
def test_conformed_views_always_fit(view: discord.ui.LayoutView):
    conform(view)
    assert_within_limits(view)


@given(views())
def test_conform_is_idempotent(view: discord.ui.LayoutView):
    conform(view)
    assert conform(view) == []


@given(views())
def test_conform_never_touches_a_fitting_view(view: discord.ui.LayoutView):
    already_fits = payload_problems(view.to_components()) == []
    interventions = conform(view)
    if already_fits:
        assert interventions == []
