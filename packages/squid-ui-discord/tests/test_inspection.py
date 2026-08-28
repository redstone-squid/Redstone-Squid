"""Read-only measurement and audit of a host-owned Discord layout."""

from dataclasses import replace
from typing import cast

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

import squid_ui as sl
import squid_ui_discord
import squid_ui_discord.target
from squid_ui.planning.limits import Axis
from squid_ui_discord import V2_LIMITS as LIMITS
from squid_ui_discord import ExistingLayoutError, LimitViolationError, conform
from squid_ui_discord.inspection import ViolationCode, audit, cost, measure


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


def _rich_view() -> discord.ui.LayoutView:
    """One view exercising every nesting shape measurement has to walk."""
    section = discord.ui.Section(accessory=discord.ui.Button(label="go", custom_id="go"))
    section.add_item(discord.ui.TextDisplay("section text"))
    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay("container text"))
    container.add_item(section)
    gallery = discord.ui.MediaGallery()
    gallery.add_item(media="https://example.invalid/a.png", description="a")
    return _view(
        discord.ui.TextDisplay("top"),
        container,
        gallery,
        _row(discord.ui.Button(label="one", custom_id="one"), discord.ui.Button(label="two", custom_id="two")),
    )


class TestAgreesWithDiscordPy:
    """Measurement must count what Discord counts, or every budget below it is fiction."""

    def test_component_count_matches_the_library_walk(self):
        view = _rich_view()
        assert measure(view).cost.get(Axis.COMPONENTS) == len(list(view.walk_children()))

    def test_component_count_matches_the_enforced_counter(self):
        view = _rich_view()
        # `_total_children` is what discord.py itself rejects at 40.
        assert measure(view).cost.get(Axis.COMPONENTS) == view._total_children

    def test_text_matches_content_length(self):
        view = _rich_view()
        assert measure(view).cost.get(Axis.DISPLAY_TEXT) == view.content_length()

    def test_section_accessory_is_counted(self):
        section = discord.ui.Section(accessory=discord.ui.Button(label="go"))
        section.add_item(discord.ui.TextDisplay("text"))
        # The section, its text, and its accessory.
        assert measure(_view(section)).cost.get(Axis.COMPONENTS) == 3


class TestMeasure:
    def test_does_not_mutate_the_view(self):
        long_text = "x" * (LIMITS.total_text + 100)
        display = discord.ui.TextDisplay(long_text)
        view = _view(display)
        measure(view)
        assert display.content == long_text

    def test_reports_the_hosts_custom_ids_with_locations(self):
        view = _rich_view()
        sites = {site.custom_id: site.path for site in measure(view).custom_ids}
        assert set(sites) == {"go", "one", "two"}
        assert sites["go"] == (1, 1, 1)  # container -> section -> accessory

    def test_external_attachments_enter_the_reservation(self):
        assert measure(_view(), attachments=3).cost.get(Axis.ATTACHMENTS) == 3

    def test_fingerprint_tracks_content(self):
        view = _rich_view()
        before = measure(view).fingerprint
        assert measure(_rich_view()).fingerprint == before
        view.add_item(discord.ui.TextDisplay("more"))
        assert measure(view).fingerprint != before

    def test_a_bare_classic_view_measures_its_controls_and_nothing_else(self):
        """It says nothing about the content or embeds the same message may also carry."""
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="a", custom_id="a"))

        reservation = measure(view)

        assert reservation.reserved.values == {Axis.CONTROLS: 1, Axis.ROWS: 1}
        assert reservation.components_v2 is False

    def test_something_that_is_neither_is_refused_by_name(self):
        with pytest.raises(TypeError, match="not str"):
            measure("a message")  # pyrefly: ignore[bad-argument-type]

    def test_invalid_host_raises_on_request(self):
        view = _view(discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)))
        with pytest.raises(ExistingLayoutError):
            measure(view).raise_if_invalid()


class TestCost:
    def test_costs_an_unattached_item(self):
        row = _row(discord.ui.Button(label="a"), discord.ui.Button(label="b"))
        assert cost(row).get(Axis.COMPONENTS) == 3
        assert cost(row).get(Axis.DISPLAY_TEXT) == 0

    def test_costs_text_it_carries(self):
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("abcd"))
        assert cost(container) == sl.planning.ResourceCost({Axis.COMPONENTS: 2, Axis.DISPLAY_TEXT: 4})

    def test_several_items_sum(self):
        one = discord.ui.TextDisplay("ab")
        two = discord.ui.TextDisplay("cde")
        assert cost(one, two).get(Axis.DISPLAY_TEXT) == 5
        assert cost(one, two).get(Axis.COMPONENTS) == 2

    def test_matches_measure_after_attachment(self):
        row = _row(discord.ui.Button(label="a"))
        view = _view(discord.ui.TextDisplay("hello"))
        before = measure(view).cost
        view.add_item(row)
        assert measure(view).cost.get(Axis.COMPONENTS) == (before + cost(row)).get(Axis.COMPONENTS)


class TestAudit:
    def test_clean_view_is_ok(self):
        report = audit(_rich_view())
        assert report.ok
        assert report.messages == ()

    def test_repairs_nothing(self):
        display = discord.ui.TextDisplay("x" * (LIMITS.total_text + 5))
        audit(_view(display))
        assert len(display.content) == LIMITS.total_text + 5

    def test_duplicate_custom_ids_are_a_hard_failure(self):
        view = _view(
            _row(discord.ui.Button(label="a", custom_id="same"), discord.ui.Button(label="b", custom_id="same"))
        )
        codes = [violation.code for violation in audit(view).violations]
        assert ViolationCode.CUSTOM_ID_DUPLICATE in codes
        assert all(not violation.repairable for violation in audit(view).violations)

    def test_attachment_overflow_is_reported(self):
        report = audit(_view(), attachments=LIMITS.attachments + 1)
        assert [v.code for v in report.violations] == [ViolationCode.ATTACHMENTS]

    def test_raise_if_invalid_names_every_violation(self):
        view = _view(
            discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)),
            _row(discord.ui.Button(label="y" * (LIMITS.components.button_label + 1))),
        )
        with pytest.raises(LimitViolationError) as excinfo:
            audit(view).raise_if_invalid()
        assert len(excinfo.value.interventions) == 2

    def test_component_overflow_is_reported_first(self):
        # discord.py refuses a 41st child, so overflow is provoked with a tighter table.
        limits = replace(LIMITS, total_components=2)
        view = _view(discord.ui.TextDisplay("a"), discord.ui.TextDisplay("b"), discord.ui.TextDisplay("c"))
        assert audit(view, limits=limits).violations[0].code is ViolationCode.TOTAL_COMPONENTS


class TestConformProjectsTheSameFindings:
    """`conform` is the repair adapter; it may not find different things than `audit`."""

    @staticmethod
    def _broken() -> discord.ui.LayoutView:
        select = discord.ui.Select(placeholder="p" * (LIMITS.components.select_placeholder + 1))
        select.options = [discord.SelectOption(label="l" * (LIMITS.components.option_label + 1), value="v")]
        return _view(
            discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)),
            _row(discord.ui.Button(label="b" * (LIMITS.components.button_label + 1))),
            _row(select),
        )

    def test_interventions_are_the_audit_messages(self):
        view = self._broken()
        expected = audit(view).messages
        assert conform(view) == list(expected)

    def test_conform_still_repairs(self):
        view = self._broken()
        conform(view)
        assert audit(view).ok


@st.composite
def _views(draw) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    for content in draw(st.lists(st.text(min_size=1, max_size=3000), max_size=5)):
        view.add_item(discord.ui.TextDisplay(content))
    labels = draw(st.lists(st.text(max_size=200), max_size=4))
    if labels:
        view.add_item(_row(*(discord.ui.Button(label=label or None) for label in labels)))
    return view


@given(_views())
def test_measurement_always_agrees_with_discordpy(view: discord.ui.LayoutView):
    reservation = measure(view)
    assert reservation.cost.get(Axis.COMPONENTS) == view._total_children
    assert reservation.cost.get(Axis.DISPLAY_TEXT) == view.content_length()


class TestReservationAxes:
    """A reservation is a smaller target, so every axis behaves the same way."""

    def test_text_reservation_shrinks_the_text_budget(self):
        target = squid_ui_discord.target.v2().reserve(squid_ui_discord.ResourceCost({Axis.DISPLAY_TEXT: 1000}))
        assert isinstance(target.limits, type(LIMITS))
        assert target.limits.total_text == LIMITS.total_text - 1000

    def test_component_reservation_shrinks_the_component_budget(self):
        target = squid_ui_discord.target.v2().reserve(squid_ui_discord.ResourceCost({Axis.COMPONENTS: 6}))
        assert isinstance(target.limits, type(LIMITS))
        assert target.limits.total_components == LIMITS.total_components - 6

    def test_local_caps_are_untouched(self):
        reserved = squid_ui_discord.target.v2().reserve(
            squid_ui_discord.ResourceCost({Axis.DISPLAY_TEXT: 500, Axis.COMPONENTS: 5, Axis.ATTACHMENTS: 2})
        )
        assert isinstance(reserved.limits, type(LIMITS))
        assert reserved.limits.components.row_buttons == LIMITS.components.row_buttons
        assert reserved.limits.section_texts == LIMITS.section_texts
        assert reserved.limits.components.select_options == LIMITS.components.select_options

    def test_unknown_resources_are_rejected(self):
        with pytest.raises(sl.errors.LayoutInvariantError, match="no reservable resource"):
            # An axis the type system cannot name; the runtime rejection is the behaviour under test.
            squid_ui_discord.target.v2().reserve(squid_ui_discord.ResourceCost({cast(Axis, "pixels"): 1}))

    def test_reservation_never_goes_negative(self):
        reserved = squid_ui_discord.target.v2().reserve(
            squid_ui_discord.ResourceCost({Axis.DISPLAY_TEXT: LIMITS.total_text * 2})
        )
        assert isinstance(reserved.limits, type(LIMITS))
        assert reserved.limits.total_text == 0

    def test_identity_is_preserved(self):
        reserved = squid_ui_discord.target.v2().reserve(squid_ui_discord.ResourceCost({Axis.COMPONENTS: 1}))
        assert reserved.id == "discord.components-v2"
        assert reserved.capabilities == squid_ui_discord.target.v2().capabilities
        assert "discord.item" in reserved.extensions


class TestReservedPlanning:
    """The planner must honour every axis it is handed, not only display text."""

    def test_text_reservation_shrinks_the_composed_view(self):
        body = sl.primitives.Text("x" * 5000)
        reserved = squid_ui_discord.render_message(
            [body], reservation=squid_ui_discord.ResourceCost({Axis.DISPLAY_TEXT: 1500})
        ).view
        assert reserved.content_length() <= LIMITS.total_text - 1500

    def test_component_reservation_is_enforced(self):
        # Twelve text components fit an unreserved message and cannot fit five, so the
        # reservation has to be the difference between composing and refusing.
        document = [sl.primitives.Text(f"line {index}") for index in range(12)]
        assert len(list(squid_ui_discord.render_message(document).view.walk_children())) == 12
        with pytest.raises(sl.errors.UnsolvableLayoutError):
            squid_ui_discord.render_message(document, reservation=squid_ui_discord.ResourceCost({Axis.COMPONENTS: 35}))

    def test_a_reserved_plan_plus_the_host_fits_the_real_budget(self):
        host = _view(discord.ui.TextDisplay("h" * 2000))
        fragment_view = squid_ui_discord.render_message(
            [sl.primitives.Text("f" * 5000)], reservation=measure(host).cost
        ).view
        combined = host.content_length() + fragment_view.content_length()
        assert combined <= LIMITS.total_text
