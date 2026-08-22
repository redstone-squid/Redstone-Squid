"""Read-only measurement and audit of a host-owned Discord layout."""

from dataclasses import replace

import discord
import pytest
from hypothesis import given
from hypothesis import strategies as st

import squid_layouts as sl
from squid_layouts.discord import DEFAULT_LIMITS as LIMITS
from squid_layouts.discord import (
    ExistingLayoutError,
    LimitViolationError,
    ViolationCode,
    audit,
    conform,
    cost,
    measure,
)


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
        assert measure(view).cost.get("components") == len(list(view.walk_children()))

    def test_component_count_matches_the_enforced_counter(self):
        view = _rich_view()
        # `_total_children` is what discord.py itself rejects at 40.
        assert measure(view).cost.get("components") == view._total_children

    def test_text_matches_content_length(self):
        view = _rich_view()
        assert measure(view).cost.get("display_text") == view.content_length()

    def test_section_accessory_is_counted(self):
        section = discord.ui.Section(accessory=discord.ui.Button(label="go"))
        section.add_item(discord.ui.TextDisplay("text"))
        # The section, its text, and its accessory.
        assert measure(_view(section)).cost.get("components") == 3


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
        assert measure(_view(), attachments=3).cost.get("attachments") == 3

    def test_fingerprint_tracks_content(self):
        view = _rich_view()
        before = measure(view).fingerprint
        assert measure(_rich_view()).fingerprint == before
        view.add_item(discord.ui.TextDisplay("more"))
        assert measure(view).fingerprint != before

    def test_classic_views_are_plan_36(self):
        with pytest.raises(TypeError, match="plan 36"):
            measure(discord.ui.View())  # pyrefly: ignore[bad-argument-type]

    def test_invalid_host_raises_on_request(self):
        view = _view(discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)))
        with pytest.raises(ExistingLayoutError):
            measure(view).raise_if_invalid()


class TestCost:
    def test_costs_an_unattached_item(self):
        row = _row(discord.ui.Button(label="a"), discord.ui.Button(label="b"))
        assert cost(row).get("components") == 3
        assert cost(row).get("display_text") == 0

    def test_costs_text_it_carries(self):
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("abcd"))
        assert cost(container) == sl.planning.ResourceCost({"components": 2, "display_text": 4})

    def test_several_items_sum(self):
        one = discord.ui.TextDisplay("ab")
        two = discord.ui.TextDisplay("cde")
        assert cost(one, two).get("display_text") == 5
        assert cost(one, two).get("components") == 2

    def test_matches_measure_after_attachment(self):
        row = _row(discord.ui.Button(label="a"))
        view = _view(discord.ui.TextDisplay("hello"))
        before = measure(view).cost
        view.add_item(row)
        assert measure(view).cost.get("components") == (before + cost(row)).get("components")


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
            _row(discord.ui.Button(label="y" * (LIMITS.button_label + 1))),
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
        select = discord.ui.Select(placeholder="p" * (LIMITS.select_placeholder + 1))
        select.options = [discord.SelectOption(label="l" * (LIMITS.option_label + 1), value="v")]
        return _view(
            discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)),
            _row(discord.ui.Button(label="b" * (LIMITS.button_label + 1))),
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
    assert reservation.cost.get("components") == view._total_children
    assert reservation.cost.get("display_text") == view.content_length()
